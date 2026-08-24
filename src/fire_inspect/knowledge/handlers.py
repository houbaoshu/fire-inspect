from __future__ import annotations

from pathlib import Path

from django.db import transaction

from fire_inspect.audit.models import AuditEvent
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.registry import register
from fire_inspect.jobs.services import (
    LostLease,
    RecoverableJobFailure,
    StaleJobInput,
    assert_active_lease,
)
from fire_inspect.knowledge.django_models import (
    KnowledgeClauseRecord,
    KnowledgeDocumentRecord,
)
from fire_inspect.knowledge.parser import (
    KnowledgeParseError,
    parse_text_docx,
    parse_text_pdf,
)
from fire_inspect.knowledge.search import normalize_text
from fire_inspect.knowledge.services import (
    KNOWLEDGE_PARSER_VERSION,
    enqueue_knowledge_embedding,
    knowledge_parse_input_fingerprint,
)


def _document_for(job) -> KnowledgeDocumentRecord:
    assert_active_lease(job_id=job.id, lease_token=job.lease_token)
    if job.scope_type != "knowledge_document" or job.inspection_id is not None:
        raise StaleJobInput("知识解析任务范围无效，本次结果未发布。")
    try:
        document = KnowledgeDocumentRecord.objects.get(pk=job.scope_id)
    except KnowledgeDocumentRecord.DoesNotExist as exc:
        raise StaleJobInput("知识文档已不存在，本次结果未发布。") from exc
    if knowledge_parse_input_fingerprint(document) != job.input_fingerprint:
        raise StaleJobInput("知识文档原件或解析规则已变化，本次结果未发布。")
    return document


def _mark_failed(job, message: str) -> None:
    with transaction.atomic():
        assert_active_lease(job_id=job.id, lease_token=job.lease_token)
        try:
            document = KnowledgeDocumentRecord.objects.select_for_update().get(pk=job.scope_id)
        except KnowledgeDocumentRecord.DoesNotExist as exc:
            raise StaleJobInput("知识文档已不存在，本次结果未发布。") from exc
        if knowledge_parse_input_fingerprint(document) != job.input_fingerprint:
            raise StaleJobInput("知识文档原件或解析规则已变化，本次结果未发布。")
        document.processing_status = KnowledgeDocumentRecord.ProcessingStatus.FAILED
        document.safe_error_message = message[:500]
        document.save(update_fields=("processing_status", "safe_error_message", "updated_at"))


def _already_published(document: KnowledgeDocumentRecord) -> bool:
    if document.processing_status != KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED:
        return False
    clauses = document.clauses.all()
    return (
        clauses.exists() and not clauses.exclude(parser_version=KNOWLEDGE_PARSER_VERSION).exists()
    )


def _publish(job, document: KnowledgeDocumentRecord, clauses) -> None:
    with transaction.atomic():
        assert_active_lease(job_id=job.id, lease_token=job.lease_token)
        try:
            locked = KnowledgeDocumentRecord.objects.select_for_update().get(pk=document.id)
        except KnowledgeDocumentRecord.DoesNotExist as exc:
            raise StaleJobInput("知识文档已不存在，本次结果未发布。") from exc
        if knowledge_parse_input_fingerprint(locked) != job.input_fingerprint:
            raise StaleJobInput("知识文档原件或解析规则已变化，本次结果未发布。")
        if _already_published(locked):
            return

        locked.clauses.all().delete()
        KnowledgeClauseRecord.objects.bulk_create(
            [
                KnowledgeClauseRecord(
                    document=locked,
                    sequence=clause.sequence,
                    chapter=clause.chapter,
                    clause_identifier=clause.clause_identifier,
                    heading=clause.heading,
                    content=clause.content,
                    page_start=clause.page_start,
                    page_end=clause.page_end,
                    source_locator={
                        "page_start": clause.page_start,
                        "page_end": clause.page_end,
                    },
                    normalized_text=normalize_text(clause.content),
                    parser_version=KNOWLEDGE_PARSER_VERSION,
                )
                for clause in clauses
            ]
        )
        locked.processing_status = KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED
        locked.safe_error_message = ""
        locked.revision += 1
        locked.save(
            update_fields=(
                "processing_status",
                "safe_error_message",
                "revision",
                "updated_at",
            )
        )
        enqueue_knowledge_embedding(record=locked, actor=job.created_by)
        AuditEvent.append(
            actor=job.created_by,
            object_type="knowledge_document",
            object_id=locked.id,
            action="parsed",
            masked_change_summary=f"knowledge parsed: clauses={len(clauses)}",
        )


@register(ProcessingJob.TaskType.KNOWLEDGE_PARSE)
def knowledge_parse_handler(job) -> None:
    document = _document_for(job)
    if _already_published(document):
        return

    try:
        KnowledgeDocumentRecord.objects.filter(pk=document.pk).update(safe_error_message="")
        source = Path(document.original_file.path)
        clauses = (
            parse_text_pdf(source, document.id)
            if document.mime_type == "application/pdf"
            else parse_text_docx(source, document.id)
        )
        _publish(job, document, clauses)
    except (LostLease, StaleJobInput):
        raise
    except KnowledgeParseError as exc:
        message = str(exc) or "未识别到可用条款，请人工检查原文后重试。"
        _mark_failed(job, message)
        raise RecoverableJobFailure(
            code="knowledge_parse_failed",
            message=message,
        ) from exc
    except OSError as exc:
        message = "知识文档原文件暂时不可用，请检查存储后重试。"
        _mark_failed(job, message)
        raise RecoverableJobFailure(
            code="knowledge_source_unavailable",
            message=message,
        ) from exc
    except Exception:
        _mark_failed(job, "知识文档解析失败，可稍后仅重试解析步骤。")
        raise
