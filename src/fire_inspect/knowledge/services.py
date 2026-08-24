from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import F

from fire_inspect.ai.config import CapabilityConfigurationError
from fire_inspect.ai.factory import provider_for
from fire_inspect.ai.providers import CapabilityUnavailable, ProviderFailure
from fire_inspect.ai.schemas import AIOutputError
from fire_inspect.audit.models import AuditEvent
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import (
    IdempotencyConflict,
    InvalidJobTransition,
    enqueue_job,
    retry_job,
)
from fire_inspect.knowledge.django_models import (
    KnowledgeClauseRecord,
    KnowledgeDocumentRecord,
    KnowledgeEmbeddingRecord,
)
from fire_inspect.knowledge.embedding import (
    cosine_similarity,
    embedding_input_fingerprint,
    validate_embedding_batch,
)
from fire_inspect.knowledge.models import (
    KnowledgeCategory,
    KnowledgeDocument,
    SourceVerification,
    Validity,
)
from fire_inspect.knowledge.search import KnowledgeIndex, SearchResult
from fire_inspect.knowledge.security import validate_knowledge_upload

KNOWLEDGE_PARSER_VERSION = "coordinate-v1"


@dataclass(frozen=True, slots=True)
class KnowledgeSearchOutcome:
    results: tuple[SearchResult, ...]
    retrieval_mode: str
    embedding_status: str
    embedding_fallback: bool


def enqueue_knowledge_embedding(
    *,
    record: KnowledgeDocumentRecord,
    actor,
    idempotency_key: str | None = None,
):
    if record.processing_status != KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED:
        raise ValueError("知识文档解析成功后才能提交 Embedding。")
    clauses = list(record.clauses.order_by("sequence", "id"))
    if not clauses:
        raise ValueError("知识文档没有可索引条款。")
    fingerprint = embedding_input_fingerprint(record, clauses)
    key = idempotency_key or f"system:knowledge-embedding:{record.id}:{fingerprint}"
    return enqueue_job(
        task_type=ProcessingJob.TaskType.EMBEDDING,
        scope_type="knowledge_document",
        scope_id=record.id,
        inspection=None,
        input_fingerprint=fingerprint,
        idempotency_key=key,
        created_by=actor,
        expected_scope_revision=record.revision,
    )


def knowledge_parse_input_fingerprint(record_or_sha) -> str:
    source_sha = getattr(record_or_sha, "sha256", record_or_sha)
    return sha256(f"knowledge-parse|{source_sha}|{KNOWLEDGE_PARSER_VERSION}".encode()).hexdigest()


def enqueue_knowledge_parse(
    *,
    record: KnowledgeDocumentRecord,
    actor,
    idempotency_key: str | None = None,
):
    fingerprint = knowledge_parse_input_fingerprint(record)
    key = idempotency_key or f"system:knowledge-parse:{record.id}:{fingerprint}"
    return enqueue_job(
        task_type=ProcessingJob.TaskType.KNOWLEDGE_PARSE,
        scope_type="knowledge_document",
        scope_id=record.id,
        inspection=None,
        input_fingerprint=fingerprint,
        idempotency_key=key,
        created_by=actor,
        # Metadata revisions do not change the immutable parse input.
        expected_scope_revision=None,
    )


@transaction.atomic
def create_knowledge_document(
    *,
    actor,
    upload,
    metadata: dict[str, object],
    idempotency_key: str | None = None,
):
    validated = validate_knowledge_upload(upload)
    category = str(metadata["category"])
    declared_internal = bool(metadata.get("declared_internal", False))
    # Reuse the pure metadata contract for classification/source invariants.
    KnowledgeDocument(
        title=str(metadata["title"]),
        category=KnowledgeCategory(category),
        declared_internal=declared_internal,
        original_path=__import__("pathlib").Path(validated["display_name"]),
        sha256=str(validated["sha256"]),
        issuer=str(metadata.get("issuer", "")),
        source_reference=str(metadata.get("source_reference", "")),
        source_verification=SourceVerification(str(metadata.get("source_verification", "pending"))),
        validity=Validity(str(metadata.get("validity", "unknown"))),
    )
    fingerprint = knowledge_parse_input_fingerprint(validated["sha256"])
    if idempotency_key:
        previous = ProcessingJob.objects.filter(idempotency_key=idempotency_key).first()
        if previous is not None:
            if (
                previous.task_type != ProcessingJob.TaskType.KNOWLEDGE_PARSE
                or previous.scope_type != "knowledge_document"
                or previous.input_fingerprint != fingerprint
                or previous.scope_id is None
            ):
                raise IdempotencyConflict("idempotency key was already used for another command")
            try:
                existing_record = KnowledgeDocumentRecord.objects.get(pk=previous.scope_id)
            except KnowledgeDocumentRecord.DoesNotExist as exc:
                raise IdempotencyConflict(
                    "idempotency key points to an unavailable command target"
                ) from exc
            requested_metadata = {
                "title": str(metadata["title"]).strip(),
                "category": category,
                "issuer": str(metadata.get("issuer", "")).strip(),
                "source_reference": str(metadata.get("source_reference", "")).strip(),
                "version": str(metadata.get("version", "")).strip(),
                "publication_date": metadata.get("publication_date"),
                "source_verification": str(metadata.get("source_verification", "pending")),
                "validity": str(metadata.get("validity", "unknown")),
            }
            if any(
                getattr(existing_record, field) != value
                for field, value in requested_metadata.items()
            ):
                raise IdempotencyConflict(
                    "idempotency key was already used with different metadata"
                )
            existing_record._processing_job = previous
            existing_record._parse_job_created = False
            return existing_record, False

    existing = KnowledgeDocumentRecord.objects.filter(sha256=validated["sha256"]).first()
    if existing:
        job, job_created = enqueue_knowledge_parse(
            record=existing,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        existing._processing_job = job
        existing._parse_job_created = job_created
        return existing, False
    record = KnowledgeDocumentRecord(
        title=str(metadata["title"]).strip(),
        category=category,
        issuer=str(metadata.get("issuer", "")).strip(),
        source_reference=str(metadata.get("source_reference", "")).strip(),
        version=str(metadata.get("version", "")).strip(),
        publication_date=metadata.get("publication_date"),
        source_verification=str(metadata.get("source_verification", "pending")),
        validity=str(metadata.get("validity", "unknown")),
        mime_type=str(validated["mime_type"]),
        sha256=str(validated["sha256"]),
        byte_size=int(validated["byte_size"]),
        uploaded_by=actor,
    )
    record.original_file.save(
        str(validated["display_name"]), ContentFile(validated["payload"]), save=False
    )
    try:
        with transaction.atomic():
            record.save(force_insert=True)
    except IntegrityError:
        record.original_file.storage.delete(record.original_file.name)
        raced = KnowledgeDocumentRecord.objects.filter(sha256=validated["sha256"]).first()
        if raced is None:
            raise
        job, job_created = enqueue_knowledge_parse(
            record=raced,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        raced._processing_job = job
        raced._parse_job_created = job_created
        return raced, False
    try:
        job, job_created = enqueue_knowledge_parse(
            record=record,
            actor=actor,
            idempotency_key=idempotency_key,
        )
        AuditEvent.append(
            actor=actor,
            object_type="knowledge_document",
            object_id=record.id,
            action="created",
            masked_change_summary=(
                f"knowledge document created: category={record.category}, bytes={record.byte_size}"
            ),
        )
    except Exception:
        record.original_file.storage.delete(record.original_file.name)
        raise
    record._processing_job = job
    record._parse_job_created = job_created
    return record, True


def _pure_document(record: KnowledgeDocumentRecord) -> KnowledgeDocument:
    return KnowledgeDocument(
        id=record.id,
        title=record.title,
        category=KnowledgeCategory(record.category),
        original_path=__import__("pathlib").Path(record.original_file.name),
        sha256=record.sha256,
        issuer=record.issuer,
        source_reference=record.source_reference,
        version=record.version,
        publication_date=record.publication_date,
        source_verification=SourceVerification(record.source_verification),
        validity=Validity(record.validity),
        declared_internal=record.category == KnowledgeDocumentRecord.Category.INTERNAL,
    )


def _pure_clause(record: KnowledgeDocumentRecord, item: KnowledgeClauseRecord):
    return __import__(
        "fire_inspect.knowledge.models", fromlist=["KnowledgeClause"]
    ).KnowledgeClause(
        id=item.id,
        document_id=record.id,
        sequence=item.sequence,
        clause_identifier=item.clause_identifier,
        content=item.content,
        page_start=item.page_start,
        page_end=item.page_end,
        heading=item.heading,
        chapter=item.chapter,
    )


def _vector_result(
    *,
    query: str,
    document: KnowledgeDocument,
    clause,
    score: float,
) -> SearchResult:
    return SearchResult(
        issue=query,
        clause_id=str(clause.id or ""),
        clause_identifier=clause.clause_identifier,
        original_text=clause.content,
        applicability_reason=("该条款由同模型向量相似度召回；请对照原文并人工判断适用性。"),
        document_title=document.title,
        document_id=str(document.id),
        page_start=clause.page_start,
        page_end=clause.page_end,
        source_status=document.source_label,
        score=round(score, 6),
        retrieval_sources=("vector",),
    )


def search_knowledge_with_status(query: str, *, limit: int = 5) -> KnowledgeSearchOutcome:
    candidates: list[SearchResult] = []
    clause_lookup: dict[str, tuple[KnowledgeDocument, object, int]] = {}
    records = list(
        KnowledgeDocumentRecord.objects.filter(
            processing_status=KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED
        ).prefetch_related("clauses")
    )
    for record in records:
        pure_document = _pure_document(record)
        clauses = [_pure_clause(record, item) for item in record.clauses.all()]
        clause_lookup.update(
            {str(clause.id): (pure_document, clause, clause.sequence) for clause in clauses}
        )
        response = KnowledgeIndex(pure_document, clauses, minimum_score=0.3).search(
            query, limit=limit
        )
        candidates.extend(response.results)

    lexical = {
        item.clause_id: replace(item, retrieval_sources=("exact_or_lexical",))
        for item in candidates
    }
    lexical_results = tuple(
        sorted(
            lexical.values(),
            key=lambda item: (
                -item.score,
                clause_lookup.get(item.clause_id, (None, None, 0))[2],
                item.clause_id,
            ),
        )[:limit]
    )

    try:
        provider = provider_for("embedding")
        query_outputs = validate_embedding_batch(provider.embed((query,)), expected_count=1)
    except (CapabilityUnavailable, CapabilityConfigurationError):
        return KnowledgeSearchOutcome(lexical_results, "lexical_fallback", "unavailable", True)
    except (ProviderFailure, AIOutputError, TypeError, ValueError):
        return KnowledgeSearchOutcome(lexical_results, "lexical_fallback", "failed", True)

    query_output = query_outputs[0]
    query_values = tuple(float(value) for value in query_output.values)
    model_fingerprint = str(query_output.model_fingerprint).strip()
    embeddings = list(
        KnowledgeEmbeddingRecord.objects.filter(
            clause_id__in=clause_lookup,
            clause__document__processing_status=(
                KnowledgeDocumentRecord.ProcessingStatus.SUCCEEDED
            ),
            input_revision=F("clause__document__revision"),
            model_fingerprint=model_fingerprint,
            status=KnowledgeEmbeddingRecord.Status.SUCCEEDED,
        ).select_related("clause__document")
    )
    if not embeddings:
        return KnowledgeSearchOutcome(lexical_results, "lexical_fallback", "not_indexed", True)
    index_complete = len(embeddings) == len(clause_lookup)

    vector_scores: dict[str, float] = {}
    try:
        for embedding in embeddings:
            if embedding.dimensions != len(query_values):
                raise ValueError("向量维度不一致")
            values = tuple(float(value) for value in embedding.values)
            if len(values) != embedding.dimensions:
                raise ValueError("已存向量维度无效")
            vector_scores[str(embedding.clause_id)] = cosine_similarity(query_values, values)
    except (TypeError, ValueError, OverflowError):
        return KnowledgeSearchOutcome(lexical_results, "lexical_fallback", "index_invalid", True)

    fused: dict[str, SearchResult] = dict(lexical)
    for clause_id, cosine in vector_scores.items():
        normalized_similarity = (cosine + 1.0) / 2.0
        existing = fused.get(clause_id)
        if existing is not None:
            score = existing.score + (1.0 - existing.score) * 0.25 * normalized_similarity
            fused[clause_id] = replace(
                existing,
                score=round(score, 6),
                retrieval_sources=("exact_or_lexical", "vector"),
            )
            continue
        # Pure vector recall must clear a deliberately conservative threshold;
        # weak similarities cannot turn a calibrated not-found case into a hit.
        if cosine < 0.78:
            continue
        document, clause, _ = clause_lookup[clause_id]
        score = 0.3 + min(0.2, (cosine - 0.78) / 0.22 * 0.2)
        fused[clause_id] = _vector_result(
            query=query,
            document=document,
            clause=clause,
            score=score,
        )

    results = tuple(
        sorted(
            fused.values(),
            key=lambda item: (
                -item.score,
                clause_lookup.get(item.clause_id, (None, None, 0))[2],
                item.clause_id,
            ),
        )[:limit]
    )
    return KnowledgeSearchOutcome(
        results,
        "hybrid",
        "succeeded" if index_complete else "partial",
        not index_complete,
    )


def search_knowledge(query: str, *, limit: int = 5) -> tuple[SearchResult, ...]:
    return search_knowledge_with_status(query, limit=limit).results


@transaction.atomic
def retry_knowledge_parse(
    *,
    record: KnowledgeDocumentRecord,
    actor,
    idempotency_key: str,
) -> KnowledgeDocumentRecord:
    record_id = record.pk
    job = (
        ProcessingJob.objects.filter(
            task_type=ProcessingJob.TaskType.KNOWLEDGE_PARSE,
            scope_type="knowledge_document",
            scope_id=record_id,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if job is None:
        raise InvalidJobTransition("knowledge parse job does not exist")
    repeated = job.last_retry_idempotency_key == idempotency_key
    job = retry_job(
        job_id=job.id,
        actor=actor,
        idempotency_key=idempotency_key,
    )
    record = KnowledgeDocumentRecord.objects.select_for_update().get(pk=record_id)
    if job.status == ProcessingJob.Status.FAILED:
        record.processing_status = KnowledgeDocumentRecord.ProcessingStatus.FAILED
        record.safe_error_message = job.safe_error_message
    else:
        record.processing_status = KnowledgeDocumentRecord.ProcessingStatus.PENDING
        record.safe_error_message = ""
    record.save(update_fields=("processing_status", "safe_error_message", "updated_at"))
    record._processing_job = job
    record._parse_job_created = True
    record._parse_retry_repeated = repeated
    return record


@transaction.atomic
def delete_knowledge_document(*, record: KnowledgeDocumentRecord, actor, reason: str) -> None:
    record_id = record.id
    storage = record.original_file.storage
    storage_name = record.original_file.name
    AuditEvent.append(
        actor=actor,
        object_type="knowledge_document",
        object_id=record_id,
        action="deleted",
        reason=reason,
        masked_change_summary="knowledge document deleted",
    )
    record.delete()
    storage.delete(storage_name)
