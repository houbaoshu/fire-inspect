from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .models import (
    DocumentDraft,
    DocumentField,
    DocumentStatus,
    DocumentType,
    DocumentVersion,
    Evidence,
    EvidenceType,
    Fact,
    FactEvidenceLink,
    FactStatus,
    Inspection,
    Material,
    Template,
    TemplateRegion,
    TemplateVersion,
    TranscriptSegment,
    User,
    ValueSource,
)
from .storage import get_storage
from .template_service import fill_template, render_docx_preview

SHARED_INSPECTION_SOURCES = {
    "inspection.unit_name",
    "inspection.address",
    "inspection.inspected_at",
}
DOCUMENT_SPECIFIC_CUSTOM_SOURCES = {"custom.photo_description"}


def is_shared_document_source(source_path: str | None) -> bool:
    if not source_path:
        return False
    return source_path in SHARED_INSPECTION_SOURCES or (
        source_path.startswith("custom.") and source_path not in DOCUMENT_SPECIFIC_CUSTOM_SOURCES
    )


def is_evidence_chain_source(source_path: str | None) -> bool:
    return bool(
        source_path
        and source_path
        in {
            "facts.confirmed_text",
            "transcript.confirmed_text",
            "document.photo_entries",
        }
    )


def _shared_datetime(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip()
    try:
        parsed = datetime.strptime(text, "%Y年%m月%d日")
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("日期应使用 YYYY年MM月DD日 或 ISO 日期时间格式") from exc
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def set_shared_inspection_value(
    inspection: Inspection, source_path: str, value: Any
) -> tuple[Any, Any]:
    if not is_shared_document_source(source_path):
        raise ValueError("该字段不是检查共享字段")
    if isinstance(value, (dict, list)):
        raise ValueError("检查共享字段必须是单值")

    if source_path.startswith("custom."):
        key = source_path.removeprefix("custom.")
        attributes = dict(inspection.dynamic_attributes or {})
        before = attributes.get(key, "")
        after = "" if value is None else value
        attributes[key] = after
        inspection.dynamic_attributes = attributes
    elif source_path == "inspection.inspected_at":
        before = inspection.inspected_at
        after = _shared_datetime(value)
        if before and after and before.date() == after.date() and len(str(value).strip()) <= 11:
            after = before
        inspection.inspected_at = after
    else:
        attribute = source_path.removeprefix("inspection.")
        before = getattr(inspection, attribute)
        after = None if value is None or str(value).strip() == "" else str(value).strip()
        setattr(inspection, attribute, after)
    if before != after:
        inspection.row_version += 1
    return before, after


def synchronize_shared_document_fields(
    db: Session,
    inspection: Inspection,
    source_path: str,
    value: Any,
    *,
    human_edit: bool,
) -> set[uuid.UUID]:
    """Apply one inspection-level value to every document view of that source."""
    rows = db.execute(
        select(DocumentField, DocumentDraft)
        .join(TemplateRegion, TemplateRegion.id == DocumentField.region_id)
        .join(DocumentDraft, DocumentDraft.id == DocumentField.document_id)
        .where(
            DocumentDraft.inspection_id == inspection.id,
            TemplateRegion.source_path == source_path,
        )
    ).all()
    affected_documents: dict[uuid.UUID, DocumentDraft] = {}
    for field, document in rows:
        changed = field.value != value or field.is_stale
        if human_edit:
            changed = changed or field.source != ValueSource.HUMAN or not field.is_locked
        if not changed:
            continue
        field.value = value
        field.is_stale = False
        if human_edit:
            field.source = ValueSource.HUMAN
            field.is_locked = True
        field.row_version += 1
        affected_documents[document.id] = document

    for document in affected_documents.values():
        if document.status == DocumentStatus.CONFIRMED:
            document.status = DocumentStatus.DRAFT
            document.confirmed_by = None
            document.confirmed_at = None
        document.row_version += 1
    return set(affected_documents)


def format_interview_qa(texts: list[str]) -> str:
    text = "\n".join(value.strip() for value in texts if value.strip()).strip()
    if not text:
        return "问：\n答："
    explicit_lines = []
    has_explicit_qa = False
    for line in text.splitlines():
        match = re.match(r"^\s*([问答])[：:]\s*(.*)$", line)
        if match:
            has_explicit_qa = True
            explicit_lines.append(f"{match.group(1)}：{match.group(2).strip()}")
        elif line.strip():
            explicit_lines.append(line.strip())
    if has_explicit_qa:
        return "\n".join(explicit_lines)

    sentences = [
        sentence.strip()
        for sentence in re.findall(r"[^。！？?!\n]+[。！？?!]?", text)
        if sentence.strip()
    ]
    if not any(sentence.endswith(("？", "?")) for sentence in sentences):
        return f"问：\n答：{text}"
    qa: list[tuple[str, str]] = []
    for sentence in sentences:
        role = "问" if sentence.endswith(("？", "?")) else "答"
        if qa and qa[-1][0] == role:
            qa[-1] = (role, f"{qa[-1][1]}{sentence}")
        else:
            qa.append((role, sentence))
    return "\n".join(f"{role}：{content}" for role, content in qa)


def concise_photo_description(text: str | None, fallback: str = "", max_length: int = 60) -> str:
    """Keep generated photo descriptions short enough for the evidence form."""
    value = str(text or fallback).strip()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"^(?:#{1,6}|[-*•])\s*", "", value).strip()
    value = re.sub(
        r"^画面中可见(?:以下)?(?:明确的)?(?:消防检查相关)?内容[：:]\s*",
        "",
        value,
    )
    value = re.sub(r"^(?:\d+[.、)]|[-*•])\s*", "", value).strip()
    if not value:
        return ""
    first_sentence = re.split(r"(?<=[。！？!?；;：:])", value, maxsplit=1)[0].strip()
    if first_sentence.endswith(("；", ";", "：", ":")):
        first_sentence = f"{first_sentence[:-1].rstrip()}。"
    if len(first_sentence) <= max_length:
        return first_sentence
    return f"{first_sentence[: max_length - 1].rstrip('，,；;：: ')}…"


def _photo_entries(
    db: Session,
    inspection: Inspection,
    selected_evidence_ids: list[uuid.UUID] | None,
) -> list[dict[str, Any]]:
    query = select(Evidence).where(
        Evidence.inspection_id == inspection.id, Evidence.type == EvidenceType.IMAGE
    )
    if selected_evidence_ids:
        query = query.where(Evidence.id.in_(selected_evidence_ids))
    evidence_items = list(db.scalars(query.order_by(Evidence.created_at)))
    if selected_evidence_ids:
        by_id = {item.id: item for item in evidence_items}
        evidence_items = [by_id[item_id] for item_id in selected_evidence_ids if item_id in by_id]
    entries = []
    for item in evidence_items:
        facts = list(
            db.scalars(
                select(Fact)
                .join(FactEvidenceLink, FactEvidenceLink.fact_id == Fact.id)
                .where(
                    FactEvidenceLink.evidence_id == item.id,
                    Fact.status == FactStatus.CONFIRMED,
                )
                .order_by(Fact.created_at)
            )
        )
        locator = item.locator or {}
        entries.append(
            {
                "evidence_id": str(item.id),
                "object_key": item.object_key,
                "caption": item.label,
                "description": concise_photo_description(locator.get("description"), item.label),
                "fact_text": "\n".join(fact.title for fact in facts),
            }
        )
    return [entry for entry in entries if entry["object_key"]]


def source_value(
    db: Session,
    inspection: Inspection,
    source_path: str | None,
    selected_evidence_ids: list[uuid.UUID] | None = None,
) -> Any:
    if source_path == "inspection.unit_name":
        return inspection.unit_name or ""
    if source_path == "inspection.address":
        return inspection.address or ""
    if source_path == "inspection.inspected_at":
        return inspection.inspected_at.strftime("%Y年%m月%d日") if inspection.inspected_at else ""
    if source_path == "facts.confirmed_text":
        facts = db.scalars(
            select(Fact)
            .where(Fact.inspection_id == inspection.id, Fact.status == FactStatus.CONFIRMED)
            .order_by(Fact.created_at)
        )
        return "\n".join(f"{index}. {fact.title}" for index, fact in enumerate(facts, start=1))
    if source_path == "transcript.confirmed_text":
        segments = list(
            db.scalars(
                select(TranscriptSegment)
                .join(Material, Material.id == TranscriptSegment.material_id)
                .where(Material.inspection_id == inspection.id, Material.deleted_at.is_(None))
                .order_by(Material.created_at, TranscriptSegment.start_ms)
            )
        )
        return format_interview_qa([segment.text for segment in segments])
    if source_path == "document.photo_entries":
        return _photo_entries(db, inspection, selected_evidence_ids)
    if source_path and source_path.startswith("custom."):
        return inspection.dynamic_attributes.get(source_path.removeprefix("custom."), "")
    return ""


def document_photo_evidence_ids(db: Session, document_id: uuid.UUID) -> list[uuid.UUID]:
    value = db.scalar(
        select(DocumentField.value)
        .join(TemplateRegion, TemplateRegion.id == DocumentField.region_id)
        .where(
            DocumentField.document_id == document_id,
            TemplateRegion.source_path == "document.photo_entries",
        )
    )
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, dict) and item.get("evidence_id"):
            result.append(uuid.UUID(str(item["evidence_id"])))
    return result


def generated_region_value(
    db: Session,
    inspection: Inspection,
    document_type: DocumentType,
    region: TemplateRegion,
    selected_evidence_ids: list[uuid.UUID] | None = None,
    photo_entries: list[dict[str, Any]] | None = None,
) -> Any:
    value = source_value(db, inspection, region.source_path, selected_evidence_ids)
    if document_type != DocumentType.PHOTO_DOCUMENT:
        return value
    entries = photo_entries or _photo_entries(db, inspection, selected_evidence_ids)
    if region.source_path == "document.photo_entries":
        return entries
    if region.source_path == "facts.confirmed_text":
        return [entry["fact_text"] for entry in entries]
    if region.source_path == "custom.photo_description":
        return [value or entry["description"] for entry in entries]
    # Location, photographer, capture time and confirmer describe the same
    # evidence set. Store them once in the editor; the template renderer reuses
    # scalar values for every repeated photo block during export.
    return value


def refresh_generated_document_fields(db: Session, document: DocumentDraft) -> None:
    inspection = db.get(Inspection, document.inspection_id)
    if not inspection:
        return
    selected_ids = (
        document_photo_evidence_ids(db, document.id)
        if document.document_type == DocumentType.PHOTO_DOCUMENT
        else []
    )
    photo_entries = (
        _photo_entries(db, inspection, selected_ids)
        if document.document_type == DocumentType.PHOTO_DOCUMENT
        else []
    )
    rows = db.execute(
        select(DocumentField, TemplateRegion)
        .join(TemplateRegion, TemplateRegion.id == DocumentField.region_id)
        .where(DocumentField.document_id == document.id)
    ).all()
    changed = False
    for field, region in rows:
        if field.source == ValueSource.HUMAN or field.is_locked:
            continue
        value = generated_region_value(
            db,
            inspection,
            document.document_type,
            region,
            selected_ids,
            photo_entries,
        )
        if field.value != value:
            field.value = value
            field.row_version += 1
            field.is_stale = False
            changed = True
    if changed:
        if document.status == DocumentStatus.CONFIRMED:
            document.status = DocumentStatus.DRAFT
            document.confirmed_by = None
            document.confirmed_at = None
        document.row_version += 1


def mark_document_fields_stale(db: Session, inspection_id: uuid.UUID, source_prefix: str) -> None:
    document_ids = select(DocumentDraft.id).where(DocumentDraft.inspection_id == inspection_id)
    region_ids = select(TemplateRegion.id).where(
        TemplateRegion.source_path.like(f"{source_prefix}%")
    )
    db.execute(
        update(DocumentField)
        .where(
            DocumentField.document_id.in_(document_ids),
            DocumentField.region_id.in_(region_ids),
        )
        .values(is_stale=True)
    )


def create_document(
    db: Session,
    inspection: Inspection,
    version: TemplateVersion,
    title: str,
    selected_evidence_ids: list[uuid.UUID],
    user: User,
) -> DocumentDraft:
    template = db.get(Template, version.template_id)
    if not template or not version.mapping_confirmed:
        raise ValueError("模板映射尚未确认")
    draft = DocumentDraft(
        inspection_id=inspection.id,
        template_version_id=version.id,
        document_type=template.document_type,
        title=title,
        created_by=user.id,
    )
    db.add(draft)
    db.flush()
    regions = list(
        db.scalars(
            select(TemplateRegion)
            .where(
                TemplateRegion.template_version_id == version.id,
                TemplateRegion.confirmed.is_(True),
            )
            .order_by(TemplateRegion.ordinal)
        )
    )
    photo_entries = (
        _photo_entries(db, inspection, selected_evidence_ids)
        if template.document_type == DocumentType.PHOTO_DOCUMENT
        else []
    )
    for region in regions:
        value = generated_region_value(
            db,
            inspection,
            template.document_type,
            region,
            selected_evidence_ids,
            photo_entries,
        )
        db.add(
            DocumentField(
                document_id=draft.id,
                region_id=region.id,
                field_key=region.field_key,
                value=value,
                source=ValueSource.GENERATED,
            )
        )
    db.commit()
    return draft


def snapshot_fields(db: Session, document_id: uuid.UUID) -> list[dict[str, Any]]:
    fields = db.scalars(
        select(DocumentField)
        .where(DocumentField.document_id == document_id)
        .order_by(DocumentField.created_at)
    )
    return [
        {
            "id": str(field.id),
            "region_id": str(field.region_id),
            "field_key": field.field_key,
            "value": field.value,
            "source": field.source.value,
            "is_locked": field.is_locked,
        }
        for field in fields
    ]


def next_document_version(db: Session, document_id: uuid.UUID) -> int:
    number = db.scalar(
        select(func.max(DocumentVersion.version_number)).where(
            DocumentVersion.document_id == document_id
        )
    )
    return (number or 0) + 1


def confirm_document(db: Session, document: DocumentDraft, user: User) -> DocumentVersion:
    document.status = DocumentStatus.CONFIRMED
    document.confirmed_by = user.id
    document.confirmed_at = datetime.now(UTC)
    document.row_version += 1
    version = DocumentVersion(
        document_id=document.id,
        version_number=next_document_version(db, document.id),
        fields_snapshot=snapshot_fields(db, document.id),
        created_by=user.id,
        confirmed=True,
    )
    db.add(version)
    db.commit()
    return version


def export_document(
    db: Session, document: DocumentDraft, user: User
) -> tuple[DocumentVersion, bytes]:
    template_version = db.get(TemplateVersion, document.template_version_id)
    if not template_version:
        raise ValueError("模板版本不存在")
    storage = get_storage()
    template_data = storage.read_bytes(template_version.normalized_object_key)
    rows = db.execute(
        select(DocumentField, TemplateRegion)
        .join(TemplateRegion, TemplateRegion.id == DocumentField.region_id)
        .where(DocumentField.document_id == document.id)
    ).all()
    fill_values = [(region, field.value) for field, region in rows]
    output, warnings = fill_template(template_data, fill_values, storage.read_bytes)
    version_number = next_document_version(db, document.id)
    key = f"inspections/{document.inspection_id}/documents/{document.id}/v{version_number}.docx"
    storage.put_bytes(
        key,
        output,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    preview_data = render_docx_preview(output)
    preview_key = None
    if preview_data:
        preview_key = (
            f"inspections/{document.inspection_id}/documents/{document.id}/v{version_number}.pdf"
        )
        storage.put_bytes(preview_key, preview_data, "application/pdf")
    version = DocumentVersion(
        document_id=document.id,
        version_number=version_number,
        fields_snapshot=snapshot_fields(db, document.id),
        object_key=key,
        preview_object_key=preview_key,
        warnings=warnings,
        created_by=user.id,
        confirmed=document.status == DocumentStatus.CONFIRMED,
    )
    db.add(version)
    db.commit()
    return version, output
