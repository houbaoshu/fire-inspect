from rest_framework import serializers

from fire_inspect.evidence.models import FactMediaRange


class FactMediaRangeSerializer(serializers.ModelSerializer):
    id = serializers.UUIDField(format="hex_verbose", read_only=True)
    material_id = serializers.UUIDField(format="hex_verbose", read_only=True)

    class Meta:
        model = FactMediaRange
        fields = ("id", "material_id", "start_ms", "end_ms", "note")


def serialize_fact_evidence(fact) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    for item in fact.media_ranges.select_related("material").order_by(
        "material_id", "start_ms", "id"
    ):
        evidence.append(
            {
                "id": str(item.id),
                "type": "media_range",
                "label": f"{item.material.display_name} · {item.start_ms / 1000:g} 秒",
                "material_id": str(item.material_id),
                "start_ms": item.start_ms,
                "end_ms": item.end_ms,
                "note": item.note,
            }
        )
    for item in fact.transcript_links.select_related("segment__transcript__material").order_by(
        "segment__start_ms", "id"
    ):
        segment = item.segment
        material = segment.transcript.material
        evidence.append(
            {
                "id": str(item.id),
                "type": "transcript",
                "label": f"转写：{material.display_name} · {segment.start_ms / 1000:g} 秒",
                "material_id": str(material.id),
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "segment_id": str(segment.id),
            }
        )
    for item in fact.keyframe_links.select_related("derivative__material").order_by(
        "derivative__source_timestamp_ms", "id"
    ):
        derivative = item.derivative
        timestamp = derivative.source_timestamp_ms or 0
        evidence.append(
            {
                "id": str(item.id),
                "type": "keyframe",
                "label": f"关键画面：{derivative.material.display_name} · {timestamp / 1000:g} 秒",
                "material_id": str(derivative.material_id),
                "start_ms": timestamp,
                "end_ms": timestamp,
                "key_frame_id": str(derivative.id),
            }
        )
    for item in fact.photo_links.select_related("photo_material").order_by("id"):
        photo = item.photo_material
        evidence.append(
            {
                "id": str(item.id),
                "type": "photo",
                "label": f"照片：{photo.display_name}",
                "material_id": str(photo.id),
                "photo_material_id": str(photo.id),
            }
        )
    for item in fact.clause_links.select_related("clause__document").order_by(
        "clause__document_id", "clause__sequence", "id"
    ):
        clause = item.clause
        evidence.append(
            {
                "id": str(item.id),
                "type": "clause",
                "label": f"{clause.document.title} {clause.clause_identifier}",
                "document_id": str(clause.document_id),
                "clause_id": str(clause.id),
                "clause_identifier": clause.clause_identifier,
            }
        )
    return evidence
