from __future__ import annotations

import uuid

from fire_inspect.evidence.models import FactMediaRange
from fire_inspect.materials.models import Material


class EvidenceValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def validate_media_range_payloads(*, inspection, payloads: list[dict]) -> list[dict]:
    material_ids: set[uuid.UUID] = set()
    for payload in payloads:
        try:
            material_ids.add(uuid.UUID(str(payload.get("material_id", ""))))
        except (AttributeError, TypeError, ValueError) as exc:
            raise EvidenceValidationError(
                "invalid_material_id", "证据材料标识格式不正确。"
            ) from exc
    materials = {
        str(material.id): material
        for material in Material.objects.filter(id__in=material_ids).select_related("inspection")
    }
    normalized: list[dict] = []
    seen: set[tuple[str, int, int]] = set()
    for payload in payloads:
        material_id = str(payload.get("material_id", ""))
        material = materials.get(material_id)
        if material is None or material.inspection_id != inspection.id:
            raise EvidenceValidationError("cross_inspection_evidence", "证据材料不属于当前检查。")
        try:
            start_ms = int(payload.get("start_ms"))
            end_ms = int(payload.get("end_ms"))
        except (TypeError, ValueError) as exc:
            raise EvidenceValidationError(
                "invalid_evidence_range", "证据时间范围格式不正确。"
            ) from exc
        if start_ms < 0 or end_ms <= start_ms:
            raise EvidenceValidationError("invalid_evidence_range", "证据时间范围无效。")
        if material.duration_ms is None:
            raise EvidenceValidationError(
                "material_duration_unknown", "材料时长未知，不能关联时间证据。"
            )
        if end_ms > material.duration_ms + 3_000:
            raise EvidenceValidationError("evidence_out_of_range", "证据时间超过材料时间轴。")
        key = (material_id, start_ms, end_ms)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "material": material,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "note": str(payload.get("note", "")).strip()[:500],
            }
        )
    return normalized


def replace_fact_media_ranges(*, fact, payloads: list[dict]) -> None:
    normalized = validate_media_range_payloads(inspection=fact.inspection, payloads=payloads)
    fact.media_ranges.all().delete()
    FactMediaRange.objects.bulk_create(
        [
            FactMediaRange(
                fact=fact,
                inspection=fact.inspection,
                material=item["material"],
                start_ms=item["start_ms"],
                end_ms=item["end_ms"],
                note=item["note"],
            )
            for item in normalized
        ]
    )


def evidence_candidates_for(*, inspection) -> list[dict[str, object]]:
    from fire_inspect.knowledge.django_models import KnowledgeClauseRecord
    from fire_inspect.materials.models import MaterialDerivative
    from fire_inspect.transcripts.models import TranscriptSegmentRecord

    candidates: list[dict[str, object]] = []
    for material in Material.objects.filter(
        inspection=inspection,
        kind__in=(Material.Kind.VIDEO, Material.Kind.AUDIO),
        duration_ms__isnull=False,
    ).order_by("created_at", "id"):
        candidates.append(
            {
                "id": f"media_range:{material.id}",
                "type": "media_range",
                "label": f"材料时间范围：{material.display_name}",
                "material_id": str(material.id),
                "start_ms": 0,
                "end_ms": material.duration_ms,
            }
        )
    segments = TranscriptSegmentRecord.objects.filter(
        transcript__material__inspection=inspection
    ).select_related("transcript__material")
    for segment in segments.order_by("transcript__material__created_at", "sequence", "id"):
        material = segment.transcript.material
        excerpt = " ".join(segment.text.split())[:40]
        candidates.append(
            {
                "id": f"transcript:{segment.id}",
                "type": "transcript",
                "label": (
                    f"转写：{material.display_name} · {segment.start_ms / 1000:g} 秒 · {excerpt}"
                ),
                "material_id": str(material.id),
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "segment_id": str(segment.id),
            }
        )
    keyframes = MaterialDerivative.objects.filter(
        material__inspection=inspection,
        kind=MaterialDerivative.Kind.KEYFRAME,
        source_timestamp_ms__isnull=False,
    ).select_related("material")
    for derivative in keyframes.order_by("material__created_at", "source_timestamp_ms", "id"):
        candidates.append(
            {
                "id": f"keyframe:{derivative.id}",
                "type": "keyframe",
                "label": (
                    f"关键画面：{derivative.material.display_name}"
                    f" · {derivative.source_timestamp_ms / 1000:g} 秒"
                ),
                "material_id": str(derivative.material_id),
                "start_ms": derivative.source_timestamp_ms,
                "key_frame_id": str(derivative.id),
            }
        )
    for photo in Material.objects.filter(inspection=inspection, kind=Material.Kind.IMAGE).order_by(
        "created_at", "id"
    ):
        candidates.append(
            {
                "id": f"photo:{photo.id}",
                "type": "photo",
                "label": f"照片：{photo.display_name}",
                "material_id": str(photo.id),
                "photo_material_id": str(photo.id),
            }
        )
    clauses = KnowledgeClauseRecord.objects.filter(
        document__processing_status="succeeded"
    ).select_related("document")
    for clause in clauses.order_by("document__title", "sequence", "id")[:200]:
        candidates.append(
            {
                "id": f"clause:{clause.id}",
                "type": "clause",
                "label": f"{clause.document.title} {clause.clause_identifier}",
                "document_id": str(clause.document_id),
                "clause_id": str(clause.id),
                "clause_identifier": clause.clause_identifier,
            }
        )
    return candidates
