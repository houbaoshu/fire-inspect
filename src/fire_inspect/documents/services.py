from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from fire_inspect.ai.factory import provider_for
from fire_inspect.ai.providers import CapabilityUnavailable, ProviderFailure
from fire_inspect.ai.schemas import DocumentDraftOutput
from fire_inspect.audit.models import AuditEvent, DomainChangeHistory
from fire_inspect.documents.domain import (
    FieldMergeResult,
    FieldOrigin,
    FieldValue,
    merge_regenerated_fields,
)
from fire_inspect.documents.generation import (
    generate_interview_docx,
    generate_photo_docx,
    generate_record_docx,
)
from fire_inspect.documents.inputs import (
    InspectionType,
    InterviewDocumentData,
    PhotoDocumentData,
    PhotoItem,
    PhotoSharedFields,
    QaItem,
    QaRole,
    RecordDocumentData,
)
from fire_inspect.documents.inspection import assert_document_structure
from fire_inspect.documents.rendering import (
    DocumentRenderError,
    finalize_interview_with_render,
    render_docx_pages,
)
from fire_inspect.evidence.serializers import serialize_fact_evidence
from fire_inspect.facts.models import Fact
from fire_inspect.inspections.models import Inspection
from fire_inspect.jobs.models import JobAttempt, ProcessingJob
from fire_inspect.jobs.services import LostLease, StaleJobInput, assert_active_lease
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.templates.models import TemplateMappingRevision, WordTemplate
from fire_inspect.templates.services import active_template_for_type, template_payload
from fire_inspect.transcripts.models import Transcript, TranscriptSegmentRecord

from .models import (
    DocumentExport,
    DocumentFactDependency,
    DocumentFieldValue,
    DocumentRevision,
    DocumentStateEvent,
    InspectionDocument,
    InterviewQaItem,
    PhotoDocumentItem,
)


class DocumentServiceError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class DocumentRevisionConflict(DocumentServiceError):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(
            "revision_conflict",
            f"文书已被其他操作更新，当前修订号为 {current_revision}。",
            status_code=409,
        )


@dataclass(frozen=True, slots=True)
class RenderedDocument:
    payload: bytes
    page_count: int
    structure_check_result: dict[str, Any]
    render_check_result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedDocumentDraft:
    output: DocumentDraftOutput
    context: dict[str, object]
    field_values: dict[str, Any]
    photo_values: dict[UUID, tuple[str, str]]
    input_revision_vector: dict[str, Any]


SHARED_FIELD_KEYS = frozenset(
    {
        "unit_name",
        "unit_address",
        "responsible_person",
        "contact_name",
        "contact_phone",
        "inspection_at",
        "inspection_type",
        "inspection_personnel",
        "general_notes",
    }
)

DOCUMENT_FIELD_DEFAULTS: dict[str, dict[str, Any]] = {
    InspectionDocument.Type.RECORD: {
        "record_number": "",
        "inspection_content": "",
    },
    InspectionDocument.Type.PHOTO: {
        "shooting_location": "",
        "photographer": "",
        "shooting_time": "",
        "confirmer": "",
    },
    InspectionDocument.Type.INTERVIEW: {
        "interview_number": 1,
        "started_at": "",
        "ended_at": "",
        "location": "",
        "interviewer_name": "",
        "interviewer_unit": "",
        "recorder_name": "",
        "recorder_unit": "",
        "interviewee_name": "",
        "gender": "",
        "age": "",
        "birth_date": "",
        "id_type": "",
        "id_number": "",
        "registered_address": "",
        "current_address": "",
        "contact": "",
    },
}


def _field_defaults(document_type: str) -> dict[str, Any]:
    try:
        return DOCUMENT_FIELD_DEFAULTS[document_type]
    except KeyError as exc:
        raise DocumentServiceError("invalid_document_type", "文书类型不正确。") from exc


def _check_revision(document: InspectionDocument, expected_revision: int) -> None:
    if document.revision != expected_revision:
        raise DocumentRevisionConflict(document.revision)


def _history(
    document: InspectionDocument,
    *,
    actor,
    action: str,
    old_values: dict[str, Any],
    new_values: dict[str, Any],
    reason: str = "",
) -> DomainChangeHistory:
    return DomainChangeHistory.objects.create(
        inspection=document.inspection,
        actor=actor,
        object_type="document",
        object_id=document.id,
        action=action,
        old_values=old_values,
        new_values=new_values,
        reason=reason,
    )


def _record_document_change(
    document: InspectionDocument,
    *,
    actor,
    action: str,
    old_values: dict[str, Any],
    new_values: dict[str, Any],
    reason: str,
    audit_summary: str,
    audit_reason: str = "",
    request_id: str = "",
) -> DomainChangeHistory:
    """Write detailed disposable history and a separate minimized permanent event."""

    history = _history(
        document,
        actor=actor,
        action=action,
        old_values=old_values,
        new_values=new_values,
        reason=reason,
    )
    AuditEvent.append(
        actor=actor,
        object_type="document",
        object_id=document.id,
        inspection_id_snapshot=document.inspection_id,
        action=action,
        reason=audit_reason,
        masked_change_summary=audit_summary,
        request_id=request_id,
    )
    return history


def _state_snapshot(document: InspectionDocument) -> dict[str, Any]:
    return {
        "state": document.state,
        "revision": document.revision,
        "confirmed_by_id": str(document.confirmed_by_id or "") or None,
        "confirmed_at": document.confirmed_at.isoformat() if document.confirmed_at else None,
        "generation_valid": document.generation_valid,
        "invalidation_reason": document.invalidation_reason,
    }


def _field_snapshot(field: DocumentFieldValue) -> dict[str, Any]:
    return {
        "id": str(field.id),
        "key": field.business_field_key,
        "value": field.value_json,
        "origin": field.origin,
        "manual_locked": field.manual_locked,
        "revision": field.revision,
    }


def _photo_item_snapshot(item: PhotoDocumentItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "sequence": item.sequence,
        "source_material_id": str(item.source_material_id or "") or None,
        "key_frame_id": str(item.key_frame_id or "") or None,
        "source_deleted": item.source_deleted,
        "source_label": item.source_label,
        "reflected_content": item.reflected_content,
        "reflected_content_origin": item.reflected_content_origin,
        "reflected_content_manual_locked": item.reflected_content_manual_locked,
        "description": item.description,
        "description_origin": item.description_origin,
        "description_manual_locked": item.description_manual_locked,
        "revision": item.revision,
    }


def _qa_item_snapshot(item: InterviewQaItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "sequence": item.sequence,
        "text": item.text,
        "suggested_role": item.suggested_role,
        "confirmed_role": item.confirmed_role,
        "content_origin": item.content_origin,
        "content_manual_locked": item.content_manual_locked,
        "role_confirmed_by_id": str(item.role_confirmed_by_id or "") or None,
        "role_confirmed_at": (
            item.role_confirmed_at.isoformat() if item.role_confirmed_at else None
        ),
        "source_segment_start_id": str(item.source_segment_start_id or "") or None,
        "source_segment_end_id": str(item.source_segment_end_id or "") or None,
        "revision": item.revision,
    }


def _document_content_snapshot(document: InspectionDocument) -> dict[str, Any]:
    return {
        "document_revision": document.revision,
        "generator_status": document.generator_status,
        "generation_valid": document.generation_valid,
        "last_generated_revision_id": str(document.last_generated_revision_id or "") or None,
        "fields": {
            field.business_field_key: _field_snapshot(field)
            for field in document.field_values.order_by("business_field_key")
        },
        "photo_items": [
            _photo_item_snapshot(item) for item in document.photo_items.order_by("sequence", "id")
        ],
        "qa_items": [
            _qa_item_snapshot(item) for item in document.qa_items.order_by("sequence", "id")
        ],
        "fact_dependency_ids": sorted(
            str(value) for value in document.fact_dependencies.values_list("fact_id", flat=True)
        ),
    }


def _create_lifecycle_revision(
    document: InspectionDocument,
    *,
    event_type: str,
    actor,
    content_snapshot: dict[str, Any],
    document_revision_no: int | None = None,
) -> DocumentRevision:
    effective_document_revision = document_revision_no or document.revision
    existing = DocumentRevision.objects.filter(
        document=document,
        event_type=event_type,
        document_revision_no=effective_document_revision,
    ).first()
    if existing is not None:
        return existing
    next_revision_no = (
        document.revisions.order_by("-revision_no").values_list("revision_no", flat=True).first()
        or 0
    ) + 1
    return DocumentRevision.objects.create(
        document=document,
        revision_no=next_revision_no,
        document_revision_no=effective_document_revision,
        event_type=event_type,
        content_snapshot=content_snapshot,
        input_revision_vector={},
        mapping_revision=document.current_mapping_revision,
        generator_version=document.generator_version,
        created_by=actor,
    )


def record_generation_requested(
    *,
    document: InspectionDocument,
    actor,
    job: ProcessingJob,
    previous_generator_status: str,
    previous_generator_job_id: UUID | None,
    previous_generation_valid: bool,
) -> None:
    _record_document_change(
        document,
        actor=actor,
        action="generation_requested",
        old_values={
            "generator_status": previous_generator_status,
            "generator_job_id": str(previous_generator_job_id or "") or None,
            "generation_valid": previous_generation_valid,
        },
        new_values={
            "generator_status": document.generator_status,
            "generator_job_id": str(job.id),
            "expected_document_revision": job.expected_scope_revision,
            "generation_valid": document.generation_valid,
        },
        reason="用户请求生成文书",
        audit_summary=(
            f"document generation requested: job_id={job.id} "
            f"expected_revision={job.expected_scope_revision}"
        ),
        request_id=str(job.id),
    )


def initialize_document_fields(
    *, document: InspectionDocument, actor=None
) -> tuple[DocumentFieldValue, ...]:
    existing = set(document.field_values.values_list("business_field_key", flat=True))
    created = DocumentFieldValue.objects.bulk_create(
        [
            DocumentFieldValue(
                document=document,
                business_field_key=key,
                value_json=value,
                origin=DocumentFieldValue.Origin.SYSTEM,
                updated_by=actor,
            )
            for key, value in _field_defaults(document.document_type).items()
            if key not in existing
        ]
    )
    return tuple(created)


@transaction.atomic
def create_document(
    *, inspection: Inspection, document_type: str, actor
) -> tuple[InspectionDocument, bool]:
    if document_type not in InspectionDocument.Type.values:
        raise DocumentServiceError("invalid_document_type", "文书类型不正确。")
    locked_inspection = Inspection.objects.select_for_update().get(pk=inspection.pk)
    existing = InspectionDocument.objects.filter(
        inspection=locked_inspection, document_type=document_type
    ).first()
    if existing is not None:
        return existing, False
    template = active_template_for_type(document_type)
    if template is None or template.active_mapping_revision is None:
        raise DocumentServiceError(
            "confirmed_template_required", "该文书类型尚无已确认模板映射。", status_code=409
        )
    mapping = template.active_mapping_revision
    document = InspectionDocument.objects.create(
        inspection=locked_inspection,
        document_type=document_type,
        name=f"{locked_inspection.unit_name}{InspectionDocument.Type(document_type).label}",
        template_version=mapping.template_version,
        current_mapping_revision=mapping,
        created_by=actor,
    )
    initialize_document_fields(document=document, actor=actor)
    confirmed_facts = Fact.objects.filter(
        inspection=locked_inspection,
        review_state=Fact.ReviewState.CONFIRMED,
    ).order_by("created_at", "id")
    DocumentFactDependency.objects.bulk_create(
        [
            DocumentFactDependency(
                document=document,
                fact_id=fact.id,
                fact_revision=fact.revision,
            )
            for fact in confirmed_facts
        ]
    )
    if document_type == InspectionDocument.Type.INTERVIEW:
        segments = TranscriptSegmentRecord.objects.filter(
            transcript__material__inspection=locked_inspection
        ).order_by(
            "transcript__material__created_at",
            "transcript_id",
            "sequence",
            "id",
        )
        InterviewQaItem.objects.bulk_create(
            [
                InterviewQaItem(
                    document=document,
                    sequence=sequence,
                    text=segment.text,
                    suggested_role=(
                        segment.confirmed_role
                        or segment.suggested_role
                        or InterviewQaItem.Role.UNKNOWN
                    ),
                    confirmed_role=segment.confirmed_role,
                    content_origin=(
                        InterviewQaItem.Origin.MANUAL
                        if segment.text_origin == Transcript.TextOrigin.MANUAL
                        else InterviewQaItem.Origin.AI
                    ),
                    content_manual_locked=segment.text_origin == Transcript.TextOrigin.MANUAL,
                    role_confirmed_by=segment.role_confirmed_by,
                    role_confirmed_at=segment.role_confirmed_at,
                    source_segment_start_id=segment.id,
                    source_segment_end_id=segment.id,
                )
                for sequence, segment in enumerate(segments, start=1)
            ]
        )
    _history(
        document,
        actor=actor,
        action="created",
        old_values={},
        new_values={"document_type": document_type, "revision": document.revision},
    )
    AuditEvent.append(
        actor=actor,
        object_type="document",
        object_id=document.id,
        inspection_id_snapshot=document.inspection_id,
        action="created",
        masked_change_summary=f"document created: type={document_type}",
    )
    return document, True


@transaction.atomic
def rename_document(
    *, document: InspectionDocument, name: str, actor, expected_revision: int
) -> InspectionDocument:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    normalized = name.strip()
    if not normalized:
        raise DocumentServiceError("document_name_required", "文书名称不能为空。")
    if len(normalized) > 300:
        raise DocumentServiceError("document_name_too_long", "文书名称过长。")
    if document.name == normalized:
        return document
    old_name = document.name
    document.name = normalized
    document.revision += 1
    document.save(update_fields=("name", "revision", "updated_at"))
    _record_document_change(
        document,
        actor=actor,
        action="renamed",
        old_values={"name": old_name},
        new_values={"name": normalized},
        reason="文书名称已修改",
        audit_summary=f"document renamed: document_revision={document.revision}",
    )
    return document


@transaction.atomic
def replace_fact_dependencies(
    *,
    document: InspectionDocument,
    fact_ids: Iterable[str | UUID],
    actor,
    expected_revision: int,
) -> InspectionDocument:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    normalized: set[UUID] = set()
    for raw in fact_ids:
        try:
            normalized.add(UUID(str(raw)))
        except (TypeError, ValueError) as exc:
            raise DocumentServiceError("invalid_fact_id", "事实标识格式不正确。") from exc
    facts = list(
        Fact.objects.filter(
            id__in=normalized,
            inspection=document.inspection,
            review_state=Fact.ReviewState.CONFIRMED,
        ).order_by("created_at", "id")
    )
    if {item.id for item in facts} != normalized:
        raise DocumentServiceError(
            "invalid_fact_dependency",
            "文书只能引用当前检查内已确认的事实。",
        )
    current = set(document.fact_dependencies.values_list("fact_id", flat=True))
    if current == normalized:
        return document
    document.fact_dependencies.all().delete()
    DocumentFactDependency.objects.bulk_create(
        [
            DocumentFactDependency(
                document=document,
                fact_id=fact.id,
                fact_revision=fact.revision,
            )
            for fact in facts
        ]
    )
    _invalidate_one_locked(
        document,
        reason=DocumentStateEvent.Reason.REFERENCED_FACT_CHANGED,
        summary="文书引用的事实集合已变更",
        caused_by_type="document_fact_dependencies",
        caused_by_id=None,
        actor=actor,
    )
    document.revision += 1
    document.generation_valid = False
    document.save(update_fields=("revision", "generation_valid", "updated_at"))
    old_fact_ids = sorted(str(value) for value in current)
    new_fact_ids = sorted(str(value) for value in normalized)
    _record_document_change(
        document,
        actor=actor,
        action="fact_dependencies_replaced",
        old_values={"fact_ids": old_fact_ids},
        new_values={"fact_ids": new_fact_ids},
        reason="文书引用的事实集合已变更",
        audit_reason=DocumentStateEvent.Reason.REFERENCED_FACT_CHANGED,
        audit_summary=(
            f"document fact dependencies replaced: old_count={len(old_fact_ids)} "
            f"new_count={len(new_fact_ids)} fact_ids={','.join(new_fact_ids)}"
        ),
    )
    return document


@transaction.atomic
def update_document_field(
    *,
    document: InspectionDocument,
    business_field_key: str,
    value: Any,
    actor,
    expected_revision: int,
    origin: str = DocumentFieldValue.Origin.MANUAL,
    manual_locked: bool = True,
) -> DocumentFieldValue:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    if business_field_key in SHARED_FIELD_KEYS:
        raise DocumentServiceError(
            "shared_field_override_forbidden",
            "该字段属于检查共享字段，请在检查信息中修改。",
        )
    if business_field_key not in _field_defaults(document.document_type):
        raise DocumentServiceError("unknown_document_field", "文书字段不存在。")
    if origin not in DocumentFieldValue.Origin.values:
        raise DocumentServiceError("invalid_field_origin", "字段来源不正确。")
    field, _ = DocumentFieldValue.objects.select_for_update().get_or_create(
        document=document,
        business_field_key=business_field_key,
        defaults={"value_json": value, "updated_by": actor},
    )
    old = {
        "value": field.value_json,
        "origin": field.origin,
        "manual_locked": field.manual_locked,
    }
    effective = old != {
        "value": value,
        "origin": origin,
        "manual_locked": bool(manual_locked),
    }
    if not effective:
        return field
    field.value_json = value
    field.origin = origin
    field.manual_locked = bool(manual_locked)
    field.revision += 1
    field.updated_by = actor
    field.save(
        update_fields=(
            "value_json",
            "origin",
            "manual_locked",
            "revision",
            "updated_by",
            "updated_at",
        )
    )
    _invalidate_one_locked(
        document,
        reason=DocumentStateEvent.Reason.DOCUMENT_FIELD_EDITED,
        summary="文书专属字段已变更",
        caused_by_type="document_field",
        caused_by_id=field.id,
        actor=actor,
    )
    document.revision += 1
    document.generation_valid = False
    document.save(update_fields=("revision", "generation_valid", "updated_at"))
    _record_document_change(
        document,
        actor=actor,
        action="field_updated",
        old_values={business_field_key: old},
        new_values={
            business_field_key: {
                "value": value,
                "origin": origin,
                "manual_locked": bool(manual_locked),
            }
        },
        reason="文书专属字段已变更",
        audit_reason=DocumentStateEvent.Reason.DOCUMENT_FIELD_EDITED,
        audit_summary=(
            f"document field updated: field_id={field.id} field_key={business_field_key} "
            f"document_revision={document.revision}"
        ),
    )
    return field


@transaction.atomic
def merge_generated_document_fields(
    *,
    document: InspectionDocument,
    generated: dict[str, Any],
    actor,
    explicit_manual_overrides: set[str] | frozenset[str] = frozenset(),
) -> FieldMergeResult:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    allowed = set(_field_defaults(document.document_type))
    unknown = set(generated) - allowed
    if unknown:
        raise DocumentServiceError("unknown_document_field", "生成结果包含未知文书字段。")
    fields = {
        item.business_field_key: item
        for item in DocumentFieldValue.objects.select_for_update().filter(document=document)
    }
    existing = {
        key: FieldValue(
            value=item.value_json,
            origin=FieldOrigin(item.origin),
            manual_locked=item.manual_locked,
        )
        for key, item in fields.items()
    }
    try:
        result = merge_regenerated_fields(
            existing,
            generated,
            explicit_manual_overrides=explicit_manual_overrides,
        )
    except ValueError as exc:
        raise DocumentServiceError("invalid_generation_result", str(exc)) from exc
    changed = False
    for key in generated:
        merged = result.fields[key]
        field = fields[key]
        desired_origin = DocumentFieldValue.Origin(merged.origin.value)
        if (
            field.value_json == merged.value
            and field.origin == desired_origin
            and field.manual_locked == merged.manual_locked
        ):
            continue
        field.value_json = merged.value
        field.origin = desired_origin
        field.manual_locked = merged.manual_locked
        field.updated_by = actor
        field.revision += 1
        field.save()
        changed = True
    if changed:
        document.revision += 1
        document.generation_valid = False
        document.save(update_fields=("revision", "generation_valid", "updated_at"))
    return result


def _invalidate_one_locked(
    document: InspectionDocument,
    *,
    reason: str,
    summary: str,
    caused_by_type: str,
    caused_by_id: UUID | None,
    actor,
) -> bool:
    if document.state != InspectionDocument.State.CONFIRMED:
        return False
    old_values = _state_snapshot(document)
    old_state = document.state
    document.state = InspectionDocument.State.DRAFT
    document.confirmed_by = None
    document.confirmed_at = None
    document.invalidation_reason = summary
    document.generation_valid = False
    document.save(
        update_fields=(
            "state",
            "confirmed_by",
            "confirmed_at",
            "invalidation_reason",
            "generation_valid",
            "updated_at",
        )
    )
    DocumentStateEvent.objects.create(
        document=document,
        from_state=old_state,
        to_state=document.state,
        reason_code=reason,
        reason_summary=summary,
        caused_by_type=caused_by_type,
        caused_by_id=caused_by_id,
        actor=actor,
    )
    new_values = _state_snapshot(document)
    new_values.update(
        {
            "revision": document.revision + 1,
            "reason_code": reason,
            "caused_by_type": caused_by_type,
            "caused_by_id": str(caused_by_id or "") or None,
        }
    )
    _record_document_change(
        document,
        actor=actor,
        action="state_invalidated",
        old_values=old_values,
        new_values=new_values,
        reason=summary,
        audit_reason=reason,
        audit_summary=(
            f"document invalidated: reason_code={reason} caused_by_type={caused_by_type} "
            f"caused_by_id={caused_by_id or ''}"
        ),
    )
    _create_lifecycle_revision(
        document,
        event_type=DocumentRevision.EventType.INVALIDATED,
        actor=actor,
        document_revision_no=document.revision + 1,
        content_snapshot={
            "from_state": old_state,
            "to_state": document.state,
            "reason_code": reason,
            "reason_summary": summary,
            "caused_by_type": caused_by_type,
            "caused_by_id": str(caused_by_id or "") or None,
        },
    )
    return True


@transaction.atomic
def invalidate_for_shared_fields(
    *, inspection: Inspection, actor, changed_fields: Iterable[str]
) -> int:
    documents = list(
        InspectionDocument.objects.select_for_update().filter(
            inspection=inspection, state=InspectionDocument.State.CONFIRMED
        )
    )
    summary = f"检查共享字段已变更：{'、'.join(changed_fields)}"
    changed = 0
    for document in documents:
        if _invalidate_one_locked(
            document,
            reason=DocumentStateEvent.Reason.SHARED_FIELDS_CHANGED,
            summary=summary,
            caused_by_type="inspection",
            caused_by_id=inspection.id,
            actor=actor,
        ):
            document.revision += 1
            document.save(update_fields=("revision", "updated_at"))
            changed += 1
    return changed


@transaction.atomic
def invalidate_for_fact(*, fact, actor, summary: str = "文书引用的事实或证据已变更") -> int:
    document_ids = DocumentFactDependency.objects.filter(fact_id=fact.id).values_list(
        "document_id", flat=True
    )
    documents = list(
        InspectionDocument.objects.select_for_update().filter(
            pk__in=document_ids, state=InspectionDocument.State.CONFIRMED
        )
    )
    changed = 0
    for document in documents:
        if _invalidate_one_locked(
            document,
            reason=DocumentStateEvent.Reason.REFERENCED_FACT_CHANGED,
            summary=summary,
            caused_by_type="fact",
            caused_by_id=fact.id,
            actor=actor,
        ):
            document.revision += 1
            document.save(update_fields=("revision", "updated_at"))
            changed += 1
    return changed


@transaction.atomic
def invalidate_for_template_mapping(
    *, template: WordTemplate, new_mapping: TemplateMappingRevision, actor
) -> int:
    documents = list(
        InspectionDocument.objects.select_for_update().filter(template_version__template=template)
    )
    changed = 0
    for document in documents:
        old_mapping_id = document.current_mapping_revision_id
        if old_mapping_id == new_mapping.id:
            continue
        invalidated = _invalidate_one_locked(
            document,
            reason=DocumentStateEvent.Reason.TEMPLATE_MAPPING_CHANGED,
            summary="文书使用的模板映射已变更",
            caused_by_type="template_mapping",
            caused_by_id=new_mapping.id,
            actor=actor,
        )
        document.current_mapping_revision = new_mapping
        document.template_version = new_mapping.template_version
        document.revision += 1
        document.generation_valid = False
        document.save(
            update_fields=(
                "current_mapping_revision",
                "template_version",
                "revision",
                "generation_valid",
                "updated_at",
            )
        )
        changed += int(invalidated)
    return changed


@transaction.atomic
def mark_material_source_deleted(*, material: Material, actor, reason: str) -> int:
    items = list(
        PhotoDocumentItem.objects.select_for_update()
        .select_related("document")
        .filter(source_material=material)
    )
    affected_documents: dict[UUID, InspectionDocument] = {}
    source_changes: dict[UUID, list[dict[str, Any]]] = {}
    for item in items:
        old_item = _photo_item_snapshot(item)
        item.source_label = material.display_name
        item.source_sha256 = material.sha256
        item.source_material = None
        item.source_deleted = True
        item.revision += 1
        item.save(
            update_fields=(
                "source_label",
                "source_sha256",
                "source_material",
                "source_deleted",
                "revision",
            )
        )
        affected_documents[item.document_id] = item.document
        source_changes.setdefault(item.document_id, []).append(
            {"old": old_item, "new": _photo_item_snapshot(item)}
        )
    for document in affected_documents.values():
        document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
        _invalidate_one_locked(
            document,
            reason=DocumentStateEvent.Reason.SOURCE_DELETED,
            summary=reason.strip() or "照片来源已删除",
            caused_by_type="material",
            caused_by_id=material.id,
            actor=actor,
        )
        document.revision += 1
        document.generation_valid = False
        document.save(update_fields=("revision", "generation_valid", "updated_at"))
        changes = source_changes[document.id]
        _record_document_change(
            document,
            actor=actor,
            action="photo_source_deleted",
            old_values={"photo_items": [value["old"] for value in changes]},
            new_values={"photo_items": [value["new"] for value in changes]},
            reason=reason.strip() or "照片来源已删除",
            audit_reason=DocumentStateEvent.Reason.SOURCE_DELETED,
            audit_summary=(
                f"document photo source deleted: material_id={material.id} "
                f"affected_item_count={len(changes)}"
            ),
        )
    return len(items)


@transaction.atomic
def add_photo_sources(
    *, document: InspectionDocument, source_ids: Iterable[str | UUID], actor, expected_revision: int
) -> InspectionDocument:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    if document.document_type != InspectionDocument.Type.PHOTO:
        raise DocumentServiceError("wrong_document_type", "只有照片文书可以添加照片。")
    unique_ids: list[UUID] = []
    seen: set[UUID] = set()
    for raw in source_ids:
        try:
            source_id = UUID(str(raw))
        except (TypeError, ValueError) as exc:
            raise DocumentServiceError("invalid_photo_source", "照片来源标识不正确。") from exc
        if source_id not in seen:
            unique_ids.append(source_id)
            seen.add(source_id)
    if not unique_ids:
        raise DocumentServiceError("photo_source_required", "请至少选择一张照片。")
    old_items = [
        _photo_item_snapshot(item) for item in document.photo_items.order_by("sequence", "id")
    ]
    existing_material_ids = set(
        document.photo_items.filter(source_material_id__in=unique_ids).values_list(
            "source_material_id", flat=True
        )
    )
    existing_keyframe_ids = set(
        document.photo_items.filter(key_frame_id__in=unique_ids).values_list(
            "key_frame_id", flat=True
        )
    )
    materials = {
        item.id: item
        for item in Material.objects.filter(
            id__in=unique_ids,
            inspection=document.inspection,
            kind=Material.Kind.IMAGE,
        )
    }
    derivatives = {
        item.id: item
        for item in MaterialDerivative.objects.select_related("material").filter(
            id__in=unique_ids,
            material__inspection=document.inspection,
            kind=MaterialDerivative.Kind.KEYFRAME,
        )
    }
    unknown = set(unique_ids) - set(materials) - set(derivatives)
    if unknown:
        raise DocumentServiceError(
            "invalid_photo_source", "照片来源不存在、不属于当前检查或不是可用图片。"
        )
    sequence = (
        document.photo_items.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    )
    created_items: list[PhotoDocumentItem] = []
    for source_id in unique_ids:
        if source_id in materials and source_id not in existing_material_ids:
            sequence += 1
            material = materials[source_id]
            created_items.append(
                PhotoDocumentItem.objects.create(
                    document=document,
                    sequence=sequence,
                    source_material=material,
                    source_label=material.display_name,
                    source_sha256=material.sha256,
                )
            )
        elif source_id in derivatives and source_id not in existing_keyframe_ids:
            sequence += 1
            derivative = derivatives[source_id]
            created_items.append(
                PhotoDocumentItem.objects.create(
                    document=document,
                    sequence=sequence,
                    key_frame_id=derivative.id,
                    source_label=derivative.material.display_name,
                    source_sha256=derivative.sha256,
                )
            )
    if created_items:
        _invalidate_one_locked(
            document,
            reason=DocumentStateEvent.Reason.PHOTO_CONTENT_CHANGED,
            summary="照片选取已变更",
            caused_by_type="photo_selection",
            caused_by_id=None,
            actor=actor,
        )
        document.revision += 1
        document.generation_valid = False
        document.save(update_fields=("revision", "generation_valid", "updated_at"))
        new_items = [
            _photo_item_snapshot(item) for item in document.photo_items.order_by("sequence", "id")
        ]
        added_item_ids = [str(item.id) for item in created_items]
        _record_document_change(
            document,
            actor=actor,
            action="photo_sources_added",
            old_values={"photo_items": old_items},
            new_values={
                "photo_items": new_items,
                "added_item_ids": added_item_ids,
            },
            reason="照片选取已变更",
            audit_reason=DocumentStateEvent.Reason.PHOTO_CONTENT_CHANGED,
            audit_summary=(
                f"document photo sources added: added_count={len(added_item_ids)} "
                f"photo_item_ids={','.join(added_item_ids)}"
            ),
        )
    return document


@transaction.atomic
def update_photo_item(
    *,
    document: InspectionDocument,
    item: PhotoDocumentItem,
    values: dict[str, Any],
    actor,
    expected_revision: int,
) -> PhotoDocumentItem:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    item = PhotoDocumentItem.objects.select_for_update().get(pk=item.pk, document=document)
    old_values = _photo_item_snapshot(item)
    changed = False
    for key in ("reflected_content", "description"):
        if key not in values:
            continue
        value = str(values[key]).strip()
        if getattr(item, key) == value:
            continue
        setattr(item, key, value)
        setattr(item, f"{key}_origin", PhotoDocumentItem.Origin.MANUAL)
        setattr(item, f"{key}_manual_locked", True)
        changed = True
    if not changed:
        return item
    item.revision += 1
    item.save()
    _invalidate_one_locked(
        document,
        reason=DocumentStateEvent.Reason.PHOTO_CONTENT_CHANGED,
        summary="逐照片内容已变更",
        caused_by_type="photo_item",
        caused_by_id=item.id,
        actor=actor,
    )
    document.revision += 1
    document.generation_valid = False
    document.save(update_fields=("revision", "generation_valid", "updated_at"))
    _record_document_change(
        document,
        actor=actor,
        action="photo_item_updated",
        old_values={"photo_item": old_values},
        new_values={"photo_item": _photo_item_snapshot(item)},
        reason="逐照片内容已变更",
        audit_reason=DocumentStateEvent.Reason.PHOTO_CONTENT_CHANGED,
        audit_summary=(
            f"document photo item updated: photo_item_id={item.id} "
            f"document_revision={document.revision}"
        ),
    )
    return item


@transaction.atomic
def delete_photo_item(
    *, document: InspectionDocument, item: PhotoDocumentItem, actor, expected_revision: int
) -> InspectionDocument:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    item = PhotoDocumentItem.objects.select_for_update().get(pk=item.pk, document=document)
    old_item = _photo_item_snapshot(item)
    old_order = [
        str(value)
        for value in document.photo_items.order_by("sequence", "id").values_list("id", flat=True)
    ]
    removed_id = item.id
    item.delete()
    for sequence, remaining in enumerate(document.photo_items.order_by("sequence", "id"), start=1):
        if remaining.sequence != sequence:
            remaining.sequence = sequence
            remaining.save(update_fields=("sequence",))
    _invalidate_one_locked(
        document,
        reason=DocumentStateEvent.Reason.PHOTO_CONTENT_CHANGED,
        summary="照片已从文书移除",
        caused_by_type="photo_item",
        caused_by_id=removed_id,
        actor=actor,
    )
    document.revision += 1
    document.generation_valid = False
    document.save(update_fields=("revision", "generation_valid", "updated_at"))
    new_order = [
        str(value)
        for value in document.photo_items.order_by("sequence", "id").values_list("id", flat=True)
    ]
    _record_document_change(
        document,
        actor=actor,
        action="photo_item_deleted",
        old_values={"photo_item": old_item, "photo_item_order": old_order},
        new_values={"deleted_photo_item_id": str(removed_id), "photo_item_order": new_order},
        reason="照片已从文书移除",
        audit_reason=DocumentStateEvent.Reason.PHOTO_CONTENT_CHANGED,
        audit_summary=(
            f"document photo item deleted: photo_item_id={removed_id} "
            f"remaining_count={len(new_order)}"
        ),
    )
    return document


@transaction.atomic
def update_qa_item(
    *,
    document: InspectionDocument,
    item: InterviewQaItem,
    values: dict[str, Any],
    actor,
    expected_revision: int,
) -> InterviewQaItem:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    if document.document_type != InspectionDocument.Type.INTERVIEW:
        raise DocumentServiceError("wrong_document_type", "只有询问笔录可以编辑问答正文。")
    item = InterviewQaItem.objects.select_for_update().get(pk=item.pk, document=document)
    old_values = _qa_item_snapshot(item)
    changed = False
    if "text" in values:
        text = str(values["text"]).strip()
        if not text:
            raise DocumentServiceError("qa_text_required", "问答正文不能为空。")
        if text != item.text:
            item.text = text
            item.content_origin = InterviewQaItem.Origin.MANUAL
            item.content_manual_locked = True
            changed = True
    if "confirmed_role" in values:
        role = values["confirmed_role"]
        if not isinstance(role, str):
            raise DocumentServiceError("qa_role_unconfirmed", "问答角色必须明确确认为问或答。")
        if role not in {InterviewQaItem.Role.QUESTION, InterviewQaItem.Role.ANSWER}:
            raise DocumentServiceError("qa_role_unconfirmed", "问答角色必须明确确认为问或答。")
        if role != item.confirmed_role:
            item.confirmed_role = role
            item.role_confirmed_by = actor
            item.role_confirmed_at = timezone.now()
            changed = True
    if not changed:
        return item
    item.revision += 1
    item.save()
    _invalidate_one_locked(
        document,
        reason=DocumentStateEvent.Reason.QA_BODY_CHANGED,
        summary="询问问答角色或正文已变更",
        caused_by_type="qa_item",
        caused_by_id=item.id,
        actor=actor,
    )
    document.revision += 1
    document.generation_valid = False
    document.save(update_fields=("revision", "generation_valid", "updated_at"))
    _record_document_change(
        document,
        actor=actor,
        action="qa_item_updated",
        old_values={"qa_item": old_values},
        new_values={"qa_item": _qa_item_snapshot(item)},
        reason="询问问答角色或正文已变更",
        audit_reason=DocumentStateEvent.Reason.QA_BODY_CHANGED,
        audit_summary=(
            f"document QA item updated: qa_item_id={item.id} document_revision={document.revision}"
        ),
    )
    return item


def _qa_segments(document: InspectionDocument):
    return (
        TranscriptSegmentRecord.objects.filter(transcript__material__inspection=document.inspection)
        .select_related("transcript", "role_confirmed_by")
        .order_by(
            "transcript__material__created_at",
            "transcript_id",
            "sequence",
            "id",
        )
    )


@transaction.atomic
def sync_qa_candidates(
    *, document: InspectionDocument, actor, expected_revision: int
) -> InspectionDocument:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    if document.document_type != InspectionDocument.Type.INTERVIEW:
        raise DocumentServiceError("wrong_document_type", "只有询问笔录可以同步问答候选。")
    old_items = [_qa_item_snapshot(item) for item in document.qa_items.order_by("sequence", "id")]
    existing = {
        item.source_segment_start_id: item
        for item in InterviewQaItem.objects.select_for_update().filter(document=document)
        if item.source_segment_start_id is not None
    }
    next_sequence = (
        document.qa_items.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    )
    changed = False
    for segment in _qa_segments(document):
        item = existing.get(segment.id)
        if item is None:
            next_sequence += 1
            InterviewQaItem.objects.create(
                document=document,
                sequence=next_sequence,
                text=segment.text,
                suggested_role=segment.confirmed_role
                or segment.suggested_role
                or InterviewQaItem.Role.UNKNOWN,
                confirmed_role=segment.confirmed_role,
                content_origin=(
                    InterviewQaItem.Origin.MANUAL
                    if segment.text_origin == Transcript.TextOrigin.MANUAL
                    else InterviewQaItem.Origin.AI
                ),
                content_manual_locked=segment.text_origin == Transcript.TextOrigin.MANUAL,
                role_confirmed_by=segment.role_confirmed_by,
                role_confirmed_at=segment.role_confirmed_at,
                source_segment_start_id=segment.id,
                source_segment_end_id=segment.id,
            )
            changed = True
            continue
        # A QA item with either human-confirmed text or role is a final human
        # choice. Explicit sync may add new candidates, but never overwrite it.
        if item.content_manual_locked or item.confirmed_role:
            continue
        desired_role = (
            segment.confirmed_role or segment.suggested_role or InterviewQaItem.Role.UNKNOWN
        )
        item_changed = False
        if item.text != segment.text:
            item.text = segment.text
            item.content_origin = (
                InterviewQaItem.Origin.MANUAL
                if segment.text_origin == Transcript.TextOrigin.MANUAL
                else InterviewQaItem.Origin.AI
            )
            item.content_manual_locked = segment.text_origin == Transcript.TextOrigin.MANUAL
            item_changed = True
        if item.suggested_role != desired_role:
            item.suggested_role = desired_role
            item_changed = True
        if segment.confirmed_role and item.confirmed_role != segment.confirmed_role:
            item.confirmed_role = segment.confirmed_role
            item.role_confirmed_by = segment.role_confirmed_by
            item.role_confirmed_at = segment.role_confirmed_at
            item_changed = True
        if item_changed:
            item.revision += 1
            item.save()
            changed = True
    if not changed:
        return document
    _invalidate_one_locked(
        document,
        reason=DocumentStateEvent.Reason.QA_BODY_CHANGED,
        summary="已从当前转写显式同步问答候选",
        caused_by_type="transcript_segments",
        caused_by_id=None,
        actor=actor,
    )
    document.revision += 1
    document.generation_valid = False
    document.save(update_fields=("revision", "generation_valid", "updated_at"))
    new_items = [_qa_item_snapshot(item) for item in document.qa_items.order_by("sequence", "id")]
    _record_document_change(
        document,
        actor=actor,
        action="qa_candidates_synced",
        old_values={"qa_items": old_items},
        new_values={"qa_items": new_items},
        reason="已从当前转写显式同步问答候选",
        audit_reason=DocumentStateEvent.Reason.QA_BODY_CHANGED,
        audit_summary=(
            f"document QA candidates synced: old_count={len(old_items)} new_count={len(new_items)}"
        ),
    )
    return document


@transaction.atomic
def create_qa_item(
    *,
    document: InspectionDocument,
    actor,
    expected_revision: int,
    text: str,
    confirmed_role: str | None,
) -> InspectionDocument:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    if document.document_type != InspectionDocument.Type.INTERVIEW:
        raise DocumentServiceError("wrong_document_type", "只有询问笔录可以添加问答。")
    normalized = str(text).strip()
    if not normalized:
        raise DocumentServiceError("qa_text_required", "问答正文不能为空。")
    role = None if confirmed_role in {None, ""} else confirmed_role
    if role not in {None, InterviewQaItem.Role.QUESTION, InterviewQaItem.Role.ANSWER}:
        raise DocumentServiceError("qa_role_invalid", "问答角色必须是问、答或未确认。")
    sequence = (
        document.qa_items.order_by("-sequence").values_list("sequence", flat=True).first() or 0
    ) + 1
    old_order = [
        str(value)
        for value in document.qa_items.order_by("sequence", "id").values_list("id", flat=True)
    ]
    created = InterviewQaItem.objects.create(
        document=document,
        sequence=sequence,
        text=normalized,
        suggested_role=role or InterviewQaItem.Role.UNKNOWN,
        confirmed_role=role,
        content_origin=InterviewQaItem.Origin.MANUAL,
        content_manual_locked=True,
        role_confirmed_by=actor if role else None,
        role_confirmed_at=timezone.now() if role else None,
    )
    _invalidate_one_locked(
        document,
        reason=DocumentStateEvent.Reason.QA_BODY_CHANGED,
        summary="人工问答已添加",
        caused_by_type="qa_item",
        caused_by_id=None,
        actor=actor,
    )
    document.revision += 1
    document.generation_valid = False
    document.save(update_fields=("revision", "generation_valid", "updated_at"))
    _record_document_change(
        document,
        actor=actor,
        action="qa_item_added",
        old_values={"qa_item_order": old_order},
        new_values={
            "qa_item": _qa_item_snapshot(created),
            "qa_item_order": [*old_order, str(created.id)],
        },
        reason="人工问答已添加",
        audit_reason=DocumentStateEvent.Reason.QA_BODY_CHANGED,
        audit_summary=(
            f"document QA item added: qa_item_id={created.id} sequence={created.sequence}"
        ),
    )
    return document


@transaction.atomic
def reorder_qa_items(
    *, document: InspectionDocument, actor, expected_revision: int, item_ids: Iterable[str | UUID]
) -> InspectionDocument:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    if document.document_type != InspectionDocument.Type.INTERVIEW:
        raise DocumentServiceError("wrong_document_type", "只有询问笔录可以调整问答顺序。")
    try:
        normalized = [UUID(str(value)) for value in item_ids]
    except (TypeError, ValueError) as exc:
        raise DocumentServiceError("invalid_qa_order", "问答顺序格式不正确。") from exc
    items = list(InterviewQaItem.objects.select_for_update().filter(document=document))
    by_id = {item.id: item for item in items}
    if len(normalized) != len(set(normalized)) or set(normalized) != set(by_id):
        raise DocumentServiceError("invalid_qa_order", "问答顺序必须完整且不能重复。")
    current = [item.id for item in sorted(items, key=lambda value: (value.sequence, value.id))]
    if current == normalized:
        return document
    offset = len(items) + max((item.sequence for item in items), default=0) + 1
    document.qa_items.update(sequence=F("sequence") + offset)
    for sequence, item_id in enumerate(normalized, start=1):
        item = by_id[item_id]
        item.sequence = sequence
        item.revision += 1
        item.save(update_fields=("sequence", "revision"))
    _invalidate_one_locked(
        document,
        reason=DocumentStateEvent.Reason.QA_BODY_CHANGED,
        summary="询问问答顺序已变更",
        caused_by_type="qa_order",
        caused_by_id=None,
        actor=actor,
    )
    document.revision += 1
    document.generation_valid = False
    document.save(update_fields=("revision", "generation_valid", "updated_at"))
    old_order = [str(value) for value in current]
    new_order = [str(value) for value in normalized]
    _record_document_change(
        document,
        actor=actor,
        action="qa_items_reordered",
        old_values={"qa_item_order": old_order},
        new_values={"qa_item_order": new_order},
        reason="询问问答顺序已变更",
        audit_reason=DocumentStateEvent.Reason.QA_BODY_CHANGED,
        audit_summary=(
            f"document QA items reordered: item_count={len(new_order)} "
            f"qa_item_ids={','.join(new_order)}"
        ),
    )
    return document


@transaction.atomic
def delete_qa_item(
    *, document: InspectionDocument, item: InterviewQaItem, actor, expected_revision: int
) -> InspectionDocument:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    if document.document_type != InspectionDocument.Type.INTERVIEW:
        raise DocumentServiceError("wrong_document_type", "只有询问笔录可以删除问答。")
    item = InterviewQaItem.objects.select_for_update().get(pk=item.pk, document=document)
    old_item = _qa_item_snapshot(item)
    old_order = [
        str(value)
        for value in document.qa_items.order_by("sequence", "id").values_list("id", flat=True)
    ]
    removed_id = item.id
    item.delete()
    for sequence, remaining in enumerate(document.qa_items.order_by("sequence", "id"), start=1):
        if remaining.sequence != sequence:
            remaining.sequence = sequence
            remaining.save(update_fields=("sequence",))
    _invalidate_one_locked(
        document,
        reason=DocumentStateEvent.Reason.QA_BODY_CHANGED,
        summary="询问问答已删除",
        caused_by_type="qa_item",
        caused_by_id=removed_id,
        actor=actor,
    )
    document.revision += 1
    document.generation_valid = False
    document.save(update_fields=("revision", "generation_valid", "updated_at"))
    new_order = [
        str(value)
        for value in document.qa_items.order_by("sequence", "id").values_list("id", flat=True)
    ]
    _record_document_change(
        document,
        actor=actor,
        action="qa_item_deleted",
        old_values={"qa_item": old_item, "qa_item_order": old_order},
        new_values={"deleted_qa_item_id": str(removed_id), "qa_item_order": new_order},
        reason="询问问答已删除",
        audit_reason=DocumentStateEvent.Reason.QA_BODY_CHANGED,
        audit_summary=(
            f"document QA item deleted: qa_item_id={removed_id} remaining_count={len(new_order)}"
        ),
    )
    return document


def _generation_facts(document: InspectionDocument) -> list[Fact]:
    dependency_ids = list(document.fact_dependencies.values_list("fact_id", flat=True))
    queryset = Fact.objects.filter(
        inspection=document.inspection,
        review_state=Fact.ReviewState.CONFIRMED,
    ).prefetch_related(
        "media_ranges__material",
        "transcript_links__segment",
        "keyframe_links__derivative",
        "photo_links__photo_material",
        "clause_links__clause__document",
    )
    # A document created before facts were confirmed has no dependency rows;
    # its first explicit generation seeds dependencies from current confirmed
    # facts. Once dependencies exist, the user's selected set is authoritative.
    if dependency_ids:
        queryset = queryset.filter(id__in=dependency_ids)
    return list(queryset.order_by("created_at", "id"))


def _document_draft_context(
    document: InspectionDocument,
) -> tuple[dict[str, object], dict[str, Any]]:
    facts = _generation_facts(document)
    fact_payloads = [
        {
            "id": str(fact.id),
            "content": fact.content,
            "revision": fact.revision,
            "needs_verification": fact.needs_verification,
            "verification_note": fact.verification_note,
            "evidence": serialize_fact_evidence(fact),
        }
        for fact in facts
    ]
    selected_photos: list[dict[str, object]] = []
    for item in document.photo_items.select_related("source_material").order_by("sequence", "id"):
        source_id = item.source_material_id or item.key_frame_id
        if source_id is None:
            continue
        related_fact_ids = []
        for fact in facts:
            matches_source = (
                item.source_material_id
                and fact.photo_links.filter(photo_material_id=item.source_material_id).exists()
            ) or (
                item.key_frame_id
                and fact.keyframe_links.filter(derivative_id=item.key_frame_id).exists()
            )
            if matches_source:
                related_fact_ids.append(str(fact.id))
        selected_photos.append(
            {
                "item_id": str(item.id),
                "source_id": str(source_id),
                "source_label": item.source_label,
                "sequence": item.sequence,
                "revision": item.revision,
                "related_fact_ids": related_fact_ids,
            }
        )
    confirmed_qa = [
        {
            "id": str(item.id),
            "sequence": item.sequence,
            "text": item.text,
            "confirmed_role": item.confirmed_role,
            "revision": item.revision,
            "source_segment_start_id": (
                str(item.source_segment_start_id) if item.source_segment_start_id else None
            ),
            "source_segment_end_id": (
                str(item.source_segment_end_id) if item.source_segment_end_id else None
            ),
        }
        for item in document.qa_items.filter(
            confirmed_role__in=(InterviewQaItem.Role.QUESTION, InterviewQaItem.Role.ANSWER)
        ).order_by("sequence", "id")
    ]
    transcript_segments = list(
        TranscriptSegmentRecord.objects.filter(
            transcript__material__inspection=document.inspection,
            confirmed_role__in=(
                TranscriptSegmentRecord.SemanticRole.QUESTION,
                TranscriptSegmentRecord.SemanticRole.ANSWER,
            ),
        )
        .select_related("transcript__material")
        .order_by("transcript__material__created_at", "transcript_id", "sequence", "id")
    )
    context: dict[str, object] = {
        "inspection": {
            "id": str(document.inspection_id),
            "unit_name": document.inspection.unit_name,
            "unit_address": document.inspection.unit_address,
            "responsible_person": document.inspection.responsible_person,
            "contact_name": document.inspection.contact_name,
            "contact_phone": document.inspection.contact_phone,
            "inspection_at": timezone.localtime(document.inspection.inspection_at).isoformat(),
            "inspection_type": document.inspection.inspection_type,
            "general_notes": document.inspection.general_notes,
            "shared_revision": document.inspection.shared_revision,
            "personnel": [
                {
                    "id": str(person.id),
                    "name": person.name,
                    "work_unit": person.work_unit,
                    "role": person.role,
                }
                for person in document.inspection.personnel.order_by("sequence", "id")
            ],
        },
        "facts": fact_payloads,
        "photo_items": selected_photos,
        "confirmed_qa_items": confirmed_qa,
        "confirmed_transcript_segments": [
            {
                "id": str(segment.id),
                "material_id": str(segment.transcript.material_id),
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": segment.text,
                "confirmed_role": segment.confirmed_role,
                "revision": segment.revision,
            }
            for segment in transcript_segments
        ],
    }
    vector = {
        "inspection_shared_revision": document.inspection.shared_revision,
        "document_revision": document.revision,
        "mapping_revision_id": str(document.current_mapping_revision_id),
        "fact_revisions": {str(fact.id): fact.revision for fact in facts},
        "photo_item_revisions": {
            str(item["item_id"]): item["revision"] for item in selected_photos
        },
        "qa_item_revisions": {str(item["id"]): item["revision"] for item in confirmed_qa},
        "confirmed_transcript_segment_revisions": {
            str(segment.id): segment.revision for segment in transcript_segments
        },
    }
    return context, vector


def document_generation_input_fingerprint(document: InspectionDocument) -> str:
    _, vector = _document_draft_context(document)
    canonical = json.dumps(vector, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _record_draft_provider(job, provider) -> None:
    if job is None:
        return
    configuration = getattr(provider, "settings", None)
    provider_name = getattr(configuration, "provider", provider.__class__.__name__)
    model_name = getattr(configuration, "model", getattr(provider, "fixture_name", ""))
    fingerprint = hashlib.sha256(
        f"{provider_name}|{getattr(configuration, 'endpoint', '')}|{model_name}".encode()
    ).hexdigest()
    JobAttempt.objects.filter(job=job, attempt_no=job.attempt_count).update(
        capability="llm",
        provider_name=str(provider_name)[:100],
        model_name=str(model_name)[:100],
        config_fingerprint=fingerprint,
    )


def _prepare_document_draft(
    document: InspectionDocument, *, provider, generation_job=None
) -> PreparedDocumentDraft:
    context, vector = _document_draft_context(document)
    _record_draft_provider(generation_job, provider)
    output = provider.draft_document(document.document_type, context)
    if not isinstance(output, DocumentDraftOutput):
        raise ProviderFailure("AI 服务返回格式无效，未保存本次结果")
    if output.document_type != document.document_type:
        raise ProviderFailure("AI 服务返回了错误的文书类型，未保存本次结果")
    allowed_fields = set(_field_defaults(document.document_type))
    if set(output.fields) - allowed_fields:
        raise ProviderFailure("AI 服务返回了未知文书字段，未保存本次结果")
    allowed_fact_ids = {UUID(str(item["id"])) for item in context["facts"]}
    if set(output.referenced_fact_ids) - allowed_fact_ids:
        raise ProviderFailure("AI 服务返回了未确认或不属于当前检查的事实引用")

    current_fields = {
        item.business_field_key: FieldValue(
            value=item.value_json,
            origin=FieldOrigin(item.origin),
            manual_locked=item.manual_locked,
        )
        for item in document.field_values.order_by("business_field_key")
    }
    try:
        merged = merge_regenerated_fields(current_fields, output.fields)
    except ValueError as exc:
        raise ProviderFailure(str(exc)) from exc
    field_values = {key: value.value for key, value in current_fields.items()}
    field_values.update({key: value.value for key, value in merged.fields.items()})

    photo_items = list(document.photo_items.order_by("sequence", "id"))
    by_source = {
        item.source_material_id or item.key_frame_id: item
        for item in photo_items
        if (item.source_material_id or item.key_frame_id) is not None
    }
    output_sources = {item.source_id for item in output.photo_items}
    if document.document_type == InspectionDocument.Type.PHOTO:
        if output_sources != set(by_source):
            raise ProviderFailure("AI 照片候选与当前选图不一致，未保存本次结果")
    elif output.photo_items:
        raise ProviderFailure("AI 服务为非照片文书返回了照片候选")
    photo_values: dict[UUID, tuple[str, str]] = {}
    for draft in output.photo_items:
        item = by_source[draft.source_id]
        reflected = (
            item.reflected_content
            if item.reflected_content_manual_locked
            else draft.reflected_content.strip()
        )
        description = (
            item.description if item.description_manual_locked else draft.description.strip()
        )
        photo_values[item.id] = (reflected, description)
    return PreparedDocumentDraft(
        output=output,
        context=context,
        field_values=field_values,
        photo_values=photo_values,
        input_revision_vector=vector,
    )


def _field_map(document: InspectionDocument) -> dict[str, Any]:
    return {
        item.business_field_key: item.value_json
        for item in document.field_values.order_by("business_field_key")
    }


def _inspection_type(inspection: Inspection) -> InspectionType:
    try:
        label = Inspection.Type(inspection.inspection_type).label
        return InspectionType(label)
    except (ValueError, KeyError) as exc:
        raise DocumentServiceError("invalid_inspection_type", "检查类型无法填入模板。") from exc


def _required_datetime(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = parse_datetime(value.strip())
    else:
        result = None
    if result is None:
        raise DocumentServiceError("document_field_required", f"{label}不能为空。")
    if timezone.is_naive(result):
        result = timezone.make_aware(result, timezone.get_current_timezone())
    return result


def _read_photo_item(item: PhotoDocumentItem) -> bytes:
    if item.source_deleted:
        raise DocumentServiceError("photo_source_deleted", "所选照片来源已删除，请重新选图。")
    try:
        if item.source_material_id is not None:
            with item.source_material.original_file.open("rb") as stream:
                return stream.read()
        if item.key_frame_id is not None:
            derivative = MaterialDerivative.objects.get(
                pk=item.key_frame_id,
                material__inspection=item.document.inspection,
                kind=MaterialDerivative.Kind.KEYFRAME,
            )
            with default_storage.open(derivative.storage_key, "rb") as stream:
                return stream.read()
    except (OSError, MaterialDerivative.DoesNotExist) as exc:
        raise DocumentServiceError("photo_source_unavailable", "所选照片暂时无法读取。") from exc
    raise DocumentServiceError("photo_source_unavailable", "所选照片暂时无法读取。")


def _generate_payload(
    document: InspectionDocument,
    *,
    field_values: dict[str, Any] | None = None,
    photo_values: dict[UUID, tuple[str, str]] | None = None,
) -> bytes:
    source = template_payload(document.template_version)
    fields = field_values if field_values is not None else _field_map(document)
    photo_values = photo_values or {}
    inspection = document.inspection
    if document.document_type == InspectionDocument.Type.RECORD:
        data = RecordDocumentData(
            document_number=str(fields.get("record_number") or ""),
            inspection_date=timezone.localtime(inspection.inspection_at).date(),
            inspection_type=_inspection_type(inspection),
            unit_name=inspection.unit_name,
            responsible_person=inspection.responsible_person,
            address=inspection.unit_address,
            contact_name=inspection.contact_name,
            contact_phone=inspection.contact_phone,
            inspection_details=str(fields.get("inspection_content") or ""),
            remarks=inspection.general_notes,
        )
        return generate_record_docx(source, data)
    if document.document_type == InspectionDocument.Type.PHOTO:
        items = tuple(
            PhotoItem(
                image=_read_photo_item(item),
                reflected_content=photo_values.get(
                    item.id, (item.reflected_content, item.description)
                )[0],
                description=photo_values.get(item.id, (item.reflected_content, item.description))[
                    1
                ],
                source_name=item.source_label,
            )
            for item in document.photo_items.select_related("source_material").order_by(
                "sequence", "id"
            )
        )
        data = PhotoDocumentData(
            items=items,
            shared=PhotoSharedFields(
                shooting_location=str(fields.get("shooting_location") or ""),
                photographer=str(fields.get("photographer") or ""),
                shooting_time=str(fields.get("shooting_time") or ""),
                confirmer=str(fields.get("confirmer") or ""),
            ),
        )
        return generate_photo_docx(source, data)
    if document.document_type == InspectionDocument.Type.INTERVIEW:
        qa_items = tuple(
            QaItem(role=QaRole(item.confirmed_role or "unknown"), text=item.text)
            for item in document.qa_items.order_by("sequence", "id")
        )
        data = InterviewDocumentData(
            interview_number=int(fields.get("interview_number") or 1),
            started_at=_required_datetime(fields.get("started_at"), "询问开始时间"),
            ended_at=_required_datetime(fields.get("ended_at"), "询问结束时间"),
            location=str(fields.get("location") or ""),
            interviewer_name=str(fields.get("interviewer_name") or ""),
            interviewer_unit=str(fields.get("interviewer_unit") or ""),
            recorder_name=str(fields.get("recorder_name") or ""),
            recorder_unit=str(fields.get("recorder_unit") or ""),
            interviewee_name=str(fields.get("interviewee_name") or ""),
            gender=str(fields.get("gender") or ""),
            age=str(fields.get("age") or ""),
            birth_date=str(fields.get("birth_date") or ""),
            id_type=str(fields.get("id_type") or ""),
            id_number=str(fields.get("id_number") or ""),
            registered_address=str(fields.get("registered_address") or ""),
            current_address=str(fields.get("current_address") or ""),
            contact=str(fields.get("contact") or ""),
            qa_items=qa_items,
        )
        return generate_interview_docx(source, data)
    raise DocumentServiceError("invalid_document_type", "文书类型不正确。")


def render_generated_document(
    payload: bytes,
    *,
    document_type: str,
    allow_font_substitution: bool | None = None,
) -> RenderedDocument:
    if allow_font_substitution is None:
        # Dev-only escape hatch; always False under FIRE_INSPECT_ENV=production.
        allow_font_substitution = settings.ALLOW_FONT_SUBSTITUTION
    with tempfile.TemporaryDirectory(prefix="fire-inspect-document-precheck-") as temporary:
        output_dir = Path(temporary) / "render"
        if document_type == InspectionDocument.Type.INTERVIEW:
            final_payload, report = finalize_interview_with_render(
                payload,
                output_dir=output_dir,
                allow_font_substitution=allow_font_substitution,
            )
        else:
            final_payload = payload
            report = render_docx_pages(
                final_payload,
                output_dir=output_dir,
                expected_type=document_type,
                allow_font_substitution=allow_font_substitution,
            )
    structure = assert_document_structure(final_payload, expected_type=document_type)
    return RenderedDocument(
        payload=final_payload,
        page_count=report.page_count,
        structure_check_result={
            "passed": True,
            "document_type": structure.document_type,
            "package_part_count": structure.package_part_count,
            "checks": list(structure.checks),
        },
        render_check_result={
            "passed": report.automated_precheck_passed,
            "font_fidelity": report.font_audit.fidelity_ready,
            "manual_review_required": report.manual_review_required,
            "warnings": list(report.warnings),
        },
    )


def _safe_generation_error(exc: Exception) -> str:
    if isinstance(exc, DocumentServiceError):
        return exc.message[:300]
    if isinstance(exc, DocumentRenderError):
        return str(exc)[:300]
    return "文书生成或渲染检查失败，请核对字段、模板和字体环境后重试。"


def _generation_error_code(exc: Exception) -> str:
    if isinstance(exc, DocumentServiceError):
        return exc.code
    if isinstance(exc, CapabilityUnavailable):
        return "capability_unavailable"
    if isinstance(exc, ProviderFailure):
        return "provider_failure"
    if isinstance(exc, DocumentRenderError):
        return "document_render_failed"
    return "document_generation_failed"


@transaction.atomic
def _mark_generation_failed(
    snapshot: InspectionDocument,
    exc: Exception,
    *,
    actor,
    generation_job: ProcessingJob | None,
) -> None:
    current = (
        InspectionDocument.objects.select_for_update()
        .select_related("inspection")
        .get(pk=snapshot.pk)
    )
    if current.revision != snapshot.revision:
        return
    job_id = str(generation_job.id) if generation_job is not None else ""
    already_recorded = False
    if job_id:
        recorded = DomainChangeHistory.objects.filter(
            object_type="document",
            object_id=current.id,
            action="generation_failed",
        ).values_list("new_values", flat=True)
        already_recorded = any(str(value.get("job_id") or "") == job_id for value in recorded)
    old_values = {
        "generator_status": current.generator_status,
        "generator_safe_error": current.generator_safe_error,
        "generation_valid": current.generation_valid,
        "job_id": job_id or None,
    }
    safe_error = _safe_generation_error(exc)
    error_code = _generation_error_code(exc)
    current.generator_status = InspectionDocument.GeneratorStatus.FAILED
    current.generator_safe_error = safe_error
    current.generation_valid = False
    current.save(
        update_fields=(
            "generator_status",
            "generator_safe_error",
            "generation_valid",
            "updated_at",
        )
    )
    if already_recorded:
        return
    _record_document_change(
        current,
        actor=actor,
        action="generation_failed",
        old_values=old_values,
        new_values={
            "generator_status": current.generator_status,
            "generator_safe_error": safe_error,
            "generation_valid": current.generation_valid,
            "error_code": error_code,
            "job_id": job_id or None,
        },
        reason="文书生成失败",
        audit_reason=error_code,
        audit_summary=(f"document generation failed: job_id={job_id} error_code={error_code}"),
        request_id=job_id,
    )


def _publish_prepared_draft(
    *, document: InspectionDocument, prepared: PreparedDocumentDraft, actor
) -> None:
    fields = {
        item.business_field_key: item
        for item in DocumentFieldValue.objects.select_for_update().filter(document=document)
    }
    for key, value in prepared.output.fields.items():
        field = fields[key]
        if field.manual_locked:
            continue
        if (
            field.value_json == value
            and field.origin == DocumentFieldValue.Origin.AI
            and field.manual_locked is False
        ):
            continue
        field.value_json = value
        field.origin = DocumentFieldValue.Origin.AI
        field.manual_locked = False
        field.updated_by = actor
        field.revision += 1
        field.save(
            update_fields=(
                "value_json",
                "origin",
                "manual_locked",
                "updated_by",
                "revision",
                "updated_at",
            )
        )
    photo_items = {
        item.id: item
        for item in PhotoDocumentItem.objects.select_for_update().filter(document=document)
    }
    for item_id, (reflected, description) in prepared.photo_values.items():
        item = photo_items[item_id]
        changed = False
        if not item.reflected_content_manual_locked and item.reflected_content != reflected:
            item.reflected_content = reflected
            item.reflected_content_origin = PhotoDocumentItem.Origin.AI
            changed = True
        if not item.description_manual_locked and item.description != description:
            item.description = description
            item.description_origin = PhotoDocumentItem.Origin.AI
            changed = True
        if changed:
            item.revision += 1
            item.save()
    facts = {
        fact.id: fact
        for fact in Fact.objects.filter(
            id__in=prepared.output.referenced_fact_ids,
            inspection=document.inspection,
            review_state=Fact.ReviewState.CONFIRMED,
        )
    }
    if set(facts) != set(prepared.output.referenced_fact_ids):
        raise DocumentServiceError(
            "generation_superseded",
            "生成期间引用事实已变化，本次结果未发布。",
            status_code=409,
        )
    document.fact_dependencies.all().delete()
    DocumentFactDependency.objects.bulk_create(
        [
            DocumentFactDependency(
                document=document,
                fact_id=fact_id,
                fact_revision=facts[fact_id].revision,
            )
            for fact_id in prepared.output.referenced_fact_ids
        ]
    )


def generate_document(
    *,
    document: InspectionDocument,
    actor,
    expected_revision: int,
    generation_job=None,
) -> InspectionDocument:
    snapshot = (
        InspectionDocument.objects.select_related(
            "inspection", "template_version", "current_mapping_revision"
        )
        .prefetch_related("field_values", "photo_items__source_material", "qa_items")
        .get(pk=document.pk)
    )
    _check_revision(snapshot, expected_revision)
    if snapshot.state == InspectionDocument.State.CONFIRMED:
        raise DocumentServiceError(
            "confirmed_document_generation_forbidden",
            "已确认文书不能在后台重新生成，请先通过显式编辑退回初稿。",
            status_code=409,
        )
    if (
        snapshot.generation_valid
        and snapshot.generator_status == InspectionDocument.GeneratorStatus.SUCCEEDED
    ):
        return snapshot
    if generation_job is not None:
        assert_active_lease(job_id=generation_job.id, lease_token=generation_job.lease_token)
        if (
            generation_job.scope_type != "document"
            or generation_job.scope_id != snapshot.id
            or generation_job.expected_scope_revision != snapshot.revision
            or generation_job.inspection_id != snapshot.inspection_id
            or snapshot.generator_job_id != generation_job.id
        ):
            raise StaleJobInput("文书生成任务与当前输入不一致，本次结果未发布")
    saved_key: str | None = None
    try:
        if (
            generation_job is not None
            and document_generation_input_fingerprint(snapshot) != generation_job.input_fingerprint
        ):
            raise StaleJobInput("文书事实、选图、问答或转写输入已变化，本次结果未发布")
        provider = provider_for("llm")
        prepared = _prepare_document_draft(
            snapshot,
            provider=provider,
            generation_job=generation_job,
        )
        payload = _generate_payload(
            snapshot,
            field_values=prepared.field_values,
            photo_values=prepared.photo_values,
        )
        rendered = render_generated_document(payload, document_type=snapshot.document_type)
        next_revision_no = (
            snapshot.revisions.order_by("-revision_no")
            .values_list("revision_no", flat=True)
            .first()
            or 0
        ) + 1
        storage_key = f"documents/{snapshot.id}/{next_revision_no}/document.docx"
        saved_key = default_storage.save(storage_key, ContentFile(rendered.payload))
        with transaction.atomic():
            if generation_job is not None:
                assert_active_lease(
                    job_id=generation_job.id,
                    lease_token=generation_job.lease_token,
                )
            locked = (
                InspectionDocument.objects.select_for_update()
                .select_related("inspection")
                .get(pk=snapshot.pk)
            )
            _check_revision(locked, snapshot.revision)
            if locked.state != InspectionDocument.State.DRAFT:
                raise DocumentServiceError(
                    "generation_superseded",
                    "生成期间文书状态已变化，本次结果未发布。",
                    status_code=409,
                )
            _, current_vector = _document_draft_context(locked)
            if current_vector != prepared.input_revision_vector:
                raise DocumentServiceError(
                    "generation_superseded", "生成期间输入已变化，本次结果未发布。", status_code=409
                )
            if generation_job is not None and locked.generator_job_id != generation_job.id:
                raise StaleJobInput("已有更新的文书生成任务，本次结果未发布")
            old_publication = _document_content_snapshot(locked)
            _publish_prepared_draft(
                document=locked,
                prepared=prepared,
                actor=actor,
            )
            new_document_revision = locked.revision + 1
            revision = DocumentRevision.objects.create(
                document=locked,
                revision_no=next_revision_no,
                document_revision_no=new_document_revision,
                event_type=DocumentRevision.EventType.GENERATED,
                content_snapshot={
                    "fields": prepared.field_values,
                    "photo_item_ids": [
                        str(value) for value in locked.photo_items.values_list("id", flat=True)
                    ],
                    "qa_item_ids": [
                        str(value) for value in locked.qa_items.values_list("id", flat=True)
                    ],
                    "fact_dependency_ids": [
                        str(value) for value in prepared.output.referenced_fact_ids
                    ],
                },
                input_revision_vector=prepared.input_revision_vector,
                mapping_revision=locked.current_mapping_revision,
                generator_version=locked.generator_version,
                generated_storage_key=saved_key,
                generated_sha256=hashlib.sha256(rendered.payload).hexdigest(),
                page_count=rendered.page_count,
                structure_check_result=rendered.structure_check_result,
                render_check_result=rendered.render_check_result,
                created_by=actor,
            )
            locked.revision = new_document_revision
            locked.last_generated_revision = revision
            locked.generator_status = InspectionDocument.GeneratorStatus.SUCCEEDED
            locked.generator_safe_error = ""
            locked.generation_valid = True
            locked.invalidation_reason = ""
            locked.save(
                update_fields=(
                    "revision",
                    "last_generated_revision",
                    "generator_status",
                    "generator_safe_error",
                    "generation_valid",
                    "invalidation_reason",
                    "updated_at",
                )
            )
            new_publication = _document_content_snapshot(locked)
            _record_document_change(
                locked,
                actor=actor,
                action="generation_published",
                old_values=old_publication,
                new_values=new_publication,
                reason="文书生成结果已原子发布",
                audit_summary=(
                    f"document generation published: generated_revision_id={revision.id} "
                    f"document_revision={locked.revision} page_count={rendered.page_count} "
                    f"fact_dependency_count={len(prepared.output.referenced_fact_ids)}"
                ),
                request_id=str(generation_job.id) if generation_job is not None else "",
            )
            return locked
    except (LostLease, StaleJobInput):
        if saved_key:
            default_storage.delete(saved_key)
        raise
    except Exception as exc:
        if saved_key:
            default_storage.delete(saved_key)
        _mark_generation_failed(
            snapshot,
            exc,
            actor=actor,
            generation_job=generation_job,
        )
        if isinstance(exc, (CapabilityUnavailable, ProviderFailure)):
            raise
        if isinstance(exc, DocumentServiceError):
            raise
        raise DocumentServiceError(
            "document_generation_failed",
            _safe_generation_error(exc),
            status_code=503,
        ) from exc


@transaction.atomic
def confirm_document(
    *, document: InspectionDocument, actor, expected_revision: int
) -> InspectionDocument:
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    if document.state == InspectionDocument.State.CONFIRMED:
        return document
    if not document.generation_valid or document.last_generated_revision_id is None:
        raise DocumentServiceError(
            "current_generation_required", "确认前必须先成功生成当前修订的文书。", status_code=409
        )
    old_values = _state_snapshot(document)
    old_state = document.state
    document.state = InspectionDocument.State.CONFIRMED
    document.confirmed_by = actor
    document.confirmed_at = timezone.now()
    document.revision += 1
    document.save(update_fields=("state", "confirmed_by", "confirmed_at", "revision", "updated_at"))
    DocumentStateEvent.objects.create(
        document=document,
        from_state=old_state,
        to_state=document.state,
        reason_code=DocumentStateEvent.Reason.USER_CONFIRMED,
        reason_summary="用户显式确认文书",
        caused_by_type="document",
        caused_by_id=document.id,
        actor=actor,
    )
    new_values = _state_snapshot(document)
    _record_document_change(
        document,
        actor=actor,
        action="state_confirmed",
        old_values=old_values,
        new_values=new_values,
        reason="用户显式确认文书",
        audit_reason=DocumentStateEvent.Reason.USER_CONFIRMED,
        audit_summary=(
            f"document confirmed: document_revision={document.revision} "
            f"generated_revision_id={document.last_generated_revision_id}"
        ),
    )
    _create_lifecycle_revision(
        document,
        event_type=DocumentRevision.EventType.CONFIRMED,
        actor=actor,
        content_snapshot={
            "from_state": old_state,
            "to_state": document.state,
            "reason_code": DocumentStateEvent.Reason.USER_CONFIRMED,
            "reason_summary": "用户显式确认文书",
            "generated_revision_id": str(document.last_generated_revision_id),
        },
    )
    return document


@transaction.atomic
def export_document(
    *, document: InspectionDocument, actor, expected_revision: int
) -> tuple[DocumentExport, bytes]:
    # Lock only the document row. PostgreSQL rejects FOR UPDATE when Django's
    # select_related() introduces the nullable last_generated_revision outer
    # join ("FOR UPDATE cannot be applied to the nullable side"). The foreign
    # key is stable while the document row lock is held, so a separate read is
    # both portable and concurrency-safe.
    document = InspectionDocument.objects.select_for_update().get(pk=document.pk)
    _check_revision(document, expected_revision)
    if document.state != InspectionDocument.State.CONFIRMED:
        raise DocumentServiceError(
            "document_confirmation_required", "只有已确认文书可以导出。", status_code=409
        )
    revision = document.last_generated_revision
    if not document.generation_valid or revision is None:
        raise DocumentServiceError("current_generation_required", "当前文书没有可导出的成功产物。")
    try:
        with default_storage.open(revision.generated_storage_key, "rb") as stream:
            payload = stream.read()
    except OSError as exc:
        raise DocumentServiceError("document_file_unavailable", "文书文件暂时不可用。") from exc
    if hashlib.sha256(payload).hexdigest() != revision.generated_sha256:
        raise DocumentServiceError("document_integrity_failed", "文书文件完整性校验失败。")
    exported, created = DocumentExport.objects.get_or_create(
        document=document,
        document_revision=revision,
        defaults={
            "storage_key": revision.generated_storage_key,
            "sha256": revision.generated_sha256,
            "page_count": revision.page_count or 1,
            "structure_check_result": revision.structure_check_result,
            "render_check_result": revision.render_check_result,
            "created_by": actor,
        },
    )
    if created:
        _record_document_change(
            document,
            actor=actor,
            action="exported",
            old_values={
                "export_id": None,
                "generated_revision_id": str(revision.id),
                "document_revision": document.revision,
            },
            new_values={
                "export_id": str(exported.id),
                "generated_revision_id": str(revision.id),
                "document_revision": document.revision,
                "sha256": exported.sha256,
                "page_count": exported.page_count,
            },
            reason="已导出当前确认文书",
            audit_summary=(
                f"document exported: export_id={exported.id} "
                f"generated_revision_id={revision.id} page_count={exported.page_count}"
            ),
        )
        _create_lifecycle_revision(
            document,
            event_type=DocumentRevision.EventType.EXPORTED,
            actor=actor,
            content_snapshot={
                "export_id": str(exported.id),
                "generated_revision_id": str(revision.id),
                "sha256": exported.sha256,
                "page_count": exported.page_count,
            },
        )
    return exported, payload
