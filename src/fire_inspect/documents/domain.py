"""Pure document-domain rules: source protection, naming and invalidation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum


class DocumentType(StrEnum):
    RECORD = "record"
    PHOTO = "photo"
    INTERVIEW = "interview"

    @property
    def chinese_name(self) -> str:
        return {
            DocumentType.RECORD: "消防监督检查记录",
            DocumentType.PHOTO: "照片文书",
            DocumentType.INTERVIEW: "询问笔录",
        }[self]


class DocumentState(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class FieldOrigin(StrEnum):
    AI = "ai"
    MANUAL = "manual"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class FieldValue:
    value: object
    origin: FieldOrigin
    manual_locked: bool = False

    def __post_init__(self) -> None:
        if self.origin is FieldOrigin.MANUAL and not self.manual_locked:
            object.__setattr__(self, "manual_locked", True)


@dataclass(frozen=True, slots=True)
class FieldMergeResult:
    fields: dict[str, FieldValue]
    preserved_manual_keys: frozenset[str]
    overwritten_manual_keys: frozenset[str]


def merge_regenerated_fields(
    existing: Mapping[str, FieldValue],
    generated: Mapping[str, object],
    *,
    explicit_manual_overrides: set[str] | frozenset[str] = frozenset(),
) -> FieldMergeResult:
    """Merge an AI regeneration while preserving every manual value by default."""

    if not generated:
        raise ValueError("文书生成返回空结果，不能标记为成功")
    unknown_overrides = set(explicit_manual_overrides) - set(generated)
    if unknown_overrides:
        raise ValueError("人工字段覆盖确认与生成结果不匹配")

    result: dict[str, FieldValue] = {}
    preserved: set[str] = set()
    overwritten: set[str] = set()
    all_keys = set(existing) | set(generated)
    for key in sorted(all_keys):
        current = existing.get(key)
        if current is not None and current.manual_locked and key not in explicit_manual_overrides:
            result[key] = current
            preserved.add(key)
            continue
        if key in generated:
            result[key] = FieldValue(generated[key], FieldOrigin.AI)
            if current is not None and current.manual_locked:
                overwritten.add(key)
            continue
        if current is not None and current.manual_locked:
            result[key] = current
            preserved.add(key)
    return FieldMergeResult(
        fields=result,
        preserved_manual_keys=frozenset(preserved),
        overwritten_manual_keys=frozenset(overwritten),
    )


class ChangeKind(StrEnum):
    SHARED_FIELDS = "shared_fields"
    FACT = "fact"
    PHOTO_CONTENT = "photo_content"
    QA_BODY = "qa_body"
    RECORD_FIELD = "record_field"
    TEMPLATE_MAPPING = "template_mapping"
    SOURCE_DELETED = "source_deleted"
    GENERATOR_UPGRADE = "generator_upgrade"


class InvalidationReason(StrEnum):
    SHARED_FIELDS_CHANGED = "SHARED_FIELDS_CHANGED"
    REFERENCED_FACT_CHANGED = "REFERENCED_FACT_CHANGED"
    PHOTO_CONTENT_CHANGED = "PHOTO_CONTENT_CHANGED"
    QA_BODY_CHANGED = "QA_BODY_CHANGED"
    TEMPLATE_MAPPING_CHANGED = "TEMPLATE_MAPPING_CHANGED"
    DOCUMENT_FIELD_EDITED = "DOCUMENT_FIELD_EDITED"
    SOURCE_DELETED = "SOURCE_DELETED"


@dataclass(frozen=True, slots=True)
class InputChange:
    kind: ChangeKind
    object_id: str
    effective: bool = True
    summary: str = ""


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    id: str
    document_type: DocumentType
    state: DocumentState
    fact_dependencies: frozenset[str] = frozenset()
    template_mapping_id: str = ""


@dataclass(frozen=True, slots=True)
class DocumentInvalidationEvent:
    document_id: str
    from_state: DocumentState
    to_state: DocumentState
    reason: InvalidationReason
    reason_summary: str
    caused_by_type: ChangeKind
    caused_by_id: str


@dataclass(frozen=True, slots=True)
class InvalidationResult:
    documents: tuple[DocumentSnapshot, ...]
    events: tuple[DocumentInvalidationEvent, ...]


def _invalidation_reason(
    document: DocumentSnapshot, change: InputChange
) -> InvalidationReason | None:
    if not change.effective or change.kind is ChangeKind.GENERATOR_UPGRADE:
        return None
    if change.kind is ChangeKind.SHARED_FIELDS:
        return InvalidationReason.SHARED_FIELDS_CHANGED
    if change.kind is ChangeKind.FACT:
        return (
            InvalidationReason.REFERENCED_FACT_CHANGED
            if change.object_id in document.fact_dependencies
            else None
        )
    if change.kind is ChangeKind.PHOTO_CONTENT:
        return (
            InvalidationReason.PHOTO_CONTENT_CHANGED
            if document.document_type is DocumentType.PHOTO
            else None
        )
    if change.kind is ChangeKind.QA_BODY:
        return (
            InvalidationReason.QA_BODY_CHANGED
            if document.document_type is DocumentType.INTERVIEW
            else None
        )
    if change.kind is ChangeKind.RECORD_FIELD:
        return (
            InvalidationReason.DOCUMENT_FIELD_EDITED
            if document.document_type is DocumentType.RECORD
            else None
        )
    if change.kind is ChangeKind.TEMPLATE_MAPPING:
        return (
            InvalidationReason.TEMPLATE_MAPPING_CHANGED
            if change.object_id == document.template_mapping_id
            else None
        )
    if change.kind is ChangeKind.SOURCE_DELETED:
        return InvalidationReason.SOURCE_DELETED
    return None


_REASON_SUMMARIES = {
    InvalidationReason.SHARED_FIELDS_CHANGED: "检查共享字段已变更",
    InvalidationReason.REFERENCED_FACT_CHANGED: "文书引用的事实或证据已变更",
    InvalidationReason.PHOTO_CONTENT_CHANGED: "照片选取、排序或逐图内容已变更",
    InvalidationReason.QA_BODY_CHANGED: "询问问答角色、文字或顺序已变更",
    InvalidationReason.TEMPLATE_MAPPING_CHANGED: "文书使用的模板映射已变更",
    InvalidationReason.DOCUMENT_FIELD_EDITED: "文书专属字段已变更",
    InvalidationReason.SOURCE_DELETED: "文书依赖的来源已删除",
}


def invalidate_documents(
    documents: tuple[DocumentSnapshot, ...] | list[DocumentSnapshot],
    change: InputChange,
) -> InvalidationResult:
    updated: list[DocumentSnapshot] = []
    events: list[DocumentInvalidationEvent] = []
    for document in documents:
        reason = _invalidation_reason(document, change)
        if reason is None or document.state is DocumentState.DRAFT:
            updated.append(document)
            continue
        updated_document = replace(document, state=DocumentState.DRAFT)
        updated.append(updated_document)
        summary = change.summary.strip() or _REASON_SUMMARIES[reason]
        events.append(
            DocumentInvalidationEvent(
                document_id=document.id,
                from_state=DocumentState.CONFIRMED,
                to_state=DocumentState.DRAFT,
                reason=reason,
                reason_summary=summary,
                caused_by_type=change.kind,
                caused_by_id=change.object_id,
            )
        )
    return InvalidationResult(documents=tuple(updated), events=tuple(events))


def format_chinese_date(value: date) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


def format_chinese_datetime(value: datetime) -> str:
    return f"{format_chinese_date(value.date())} {value.hour:02d}:{value.minute:02d}"


def new_document_name(unit_name: str, document_type: DocumentType) -> str:
    normalized = unit_name.strip()
    if not normalized:
        raise ValueError("单位名称不能为空")
    return f"{normalized}{document_type.chinese_name}"
