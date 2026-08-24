from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from fire_inspect.ai.schemas import SemanticRole, TranscriptOutput
from fire_inspect.audit.models import AuditEvent, DomainChangeHistory
from fire_inspect.materials.models import Material
from fire_inspect.transcripts.models import Transcript, TranscriptSegmentRecord
from fire_inspect.transcripts.signals import transcript_changed


class RevisionConflict(Exception):
    def __init__(self, current_revision: int):
        self.current_revision = current_revision
        super().__init__(f"current revision is {current_revision}")


class ManualTranscriptProtected(Exception):
    pass


class TranscriptAlreadyExists(Exception):
    pass


_UNSET = object()


def _segment_snapshot(segment: TranscriptSegmentRecord) -> dict[str, Any]:
    return {
        "id": str(segment.id),
        "sequence": segment.sequence,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "text": segment.text,
        "suggested_role": segment.suggested_role,
        "confirmed_role": segment.confirmed_role,
        "text_origin": segment.text_origin,
        "revision": segment.revision,
    }


def _record_change(
    *, transcript: Transcript, actor, action: str, old_values: dict, new_values: dict
) -> None:
    DomainChangeHistory.objects.create(
        inspection=transcript.material.inspection,
        actor=actor,
        object_type="transcript",
        object_id=transcript.id,
        action=action,
        old_values=old_values,
        new_values=new_values,
    )
    AuditEvent.append(
        actor=actor,
        object_type="transcript",
        object_id=transcript.id,
        inspection_id_snapshot=transcript.material.inspection_id,
        action=action,
        masked_change_summary=f"transcript changed: action={action}",
    )
    transcript_changed.send(
        sender=Transcript,
        transcript=transcript,
        actor=actor,
        action=action,
        old_values=old_values,
        new_values=new_values,
    )


@transaction.atomic
def publish_transcript_output(
    output: TranscriptOutput, *, updated_by=None, language: str = "zh-CN"
) -> tuple[Transcript, bool]:
    material = (
        Material.objects.select_for_update().select_related("inspection").get(pk=output.material_id)
    )
    if material.kind not in {Material.Kind.VIDEO, Material.Kind.AUDIO}:
        raise ValueError("只有视频或录音材料可以发布转写")
    if not material.duration_ms:
        raise ValueError("材料时长未知，不能发布时间锚转写")
    output.validate_timeline(material.duration_ms)
    transcript = Transcript.objects.filter(material=material).first()
    if transcript is None:
        transcript = Transcript.objects.create(
            material=material,
            full_text=output.full_text,
            full_text_origin=Transcript.TextOrigin.AI,
            language=language,
            updated_by=updated_by,
        )
        TranscriptSegmentRecord.objects.bulk_create(
            [
                TranscriptSegmentRecord(
                    transcript=transcript,
                    sequence=sequence,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=segment.text,
                    suggested_role=segment.suggested_role.value,
                )
                for sequence, segment in enumerate(output.segments, start=1)
            ]
        )
        created_segments = list(transcript.segments.order_by("sequence"))
        _record_change(
            transcript=transcript,
            actor=updated_by,
            action="created",
            old_values={},
            new_values={
                "full_text": transcript.full_text,
                "segments": [_segment_snapshot(segment) for segment in created_segments],
                "revision": transcript.revision,
            },
        )
        return transcript, True

    existing_segments = list(transcript.segments.order_by("sequence"))
    same = transcript.full_text == output.full_text and len(existing_segments) == len(
        output.segments
    )
    if same:
        same = all(
            current.start_ms == incoming.start_ms
            and current.end_ms == incoming.end_ms
            and current.text == incoming.text
            and current.suggested_role == incoming.suggested_role.value
            for current, incoming in zip(existing_segments, output.segments, strict=True)
        )
    if same:
        return transcript, False
    if transcript.full_text_origin == Transcript.TextOrigin.MANUAL or any(
        segment.text_origin == Transcript.TextOrigin.MANUAL or segment.confirmed_role
        for segment in existing_segments
    ):
        raise ManualTranscriptProtected("人工转写内容不能被后台结果覆盖")
    old_values = {
        "full_text": transcript.full_text,
        "segments": [_segment_snapshot(segment) for segment in existing_segments],
    }
    transcript.full_text = output.full_text
    transcript.language = language
    transcript.updated_by = updated_by
    transcript.revision += 1
    transcript.save(update_fields=("full_text", "language", "updated_by", "revision", "updated_at"))
    transcript.segments.all().delete()
    TranscriptSegmentRecord.objects.bulk_create(
        [
            TranscriptSegmentRecord(
                transcript=transcript,
                sequence=sequence,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                text=segment.text,
                suggested_role=segment.suggested_role.value,
            )
            for sequence, segment in enumerate(output.segments, start=1)
        ]
    )
    _record_change(
        transcript=transcript,
        actor=updated_by,
        action="ai_replaced",
        old_values=old_values,
        new_values={"revision": transcript.revision},
    )
    return transcript, False


@transaction.atomic
def create_manual_transcript(
    *,
    material_id,
    actor,
    full_text: str,
    segments: list[dict[str, Any]] | None = None,
    language: str = "zh-CN",
) -> Transcript:
    """Create a time-anchored manual fallback when ASR is unavailable."""

    material = Material.objects.select_for_update().select_related("inspection").get(pk=material_id)
    if material.kind not in {Material.Kind.VIDEO, Material.Kind.AUDIO}:
        raise ValueError("只有视频或录音材料可以补充转写")
    if not material.duration_ms:
        raise ValueError("材料时长未知，请先完成媒体探测")
    if Transcript.objects.filter(material=material).exists():
        raise TranscriptAlreadyExists("该材料已有转写，请在现有转写上编辑")

    normalized_full_text = str(full_text or "").strip()
    raw_segments = segments or []
    if not normalized_full_text and raw_segments:
        normalized_full_text = "\n".join(str(item.get("text", "")).strip() for item in raw_segments)
    if not normalized_full_text:
        raise ValueError("完整转写不能为空")
    if not raw_segments:
        raw_segments = [
            {
                "start_ms": 0,
                "end_ms": material.duration_ms,
                "text": normalized_full_text,
                "confirmed_role": None,
            }
        ]

    normalized_segments: list[dict[str, Any]] = []
    previous_start = -1
    for item in raw_segments:
        if not isinstance(item, dict):
            raise ValueError("转写片段格式无效")
        try:
            start_ms = int(item.get("start_ms"))
            end_ms = int(item.get("end_ms"))
        except (TypeError, ValueError) as exc:
            raise ValueError("转写片段时间范围无效") from exc
        text = str(item.get("text", "")).strip()
        role = item.get("confirmed_role") or None
        if start_ms < 0 or end_ms <= start_ms or end_ms > material.duration_ms + 3_000:
            raise ValueError("转写片段时间范围无效")
        if start_ms < previous_start:
            raise ValueError("转写片段必须按时间排序")
        if not text:
            raise ValueError("转写片段不能为空")
        if role not in {None, "question", "answer"}:
            raise ValueError("人工确认角色必须是问、答或未确认")
        previous_start = start_ms
        normalized_segments.append(
            {
                "start_ms": start_ms,
                "end_ms": end_ms,
                "text": text,
                "confirmed_role": role,
            }
        )
    if normalized_segments[0]["start_ms"] > 3_000 or (
        normalized_segments[-1]["end_ms"] < material.duration_ms - 3_000
    ):
        raise ValueError("转写片段必须覆盖材料时间范围")

    transcript = Transcript.objects.create(
        material=material,
        full_text=normalized_full_text,
        full_text_origin=Transcript.TextOrigin.MANUAL,
        language=str(language or "zh-CN")[:20],
        updated_by=actor,
    )
    now = timezone.now()
    created_segments = TranscriptSegmentRecord.objects.bulk_create(
        [
            TranscriptSegmentRecord(
                transcript=transcript,
                sequence=sequence,
                start_ms=item["start_ms"],
                end_ms=item["end_ms"],
                text=item["text"],
                suggested_role=TranscriptSegmentRecord.SemanticRole.UNKNOWN,
                confirmed_role=item["confirmed_role"],
                text_origin=Transcript.TextOrigin.MANUAL,
                role_confirmed_by=actor if item["confirmed_role"] else None,
                role_confirmed_at=now if item["confirmed_role"] else None,
            )
            for sequence, item in enumerate(normalized_segments, start=1)
        ]
    )
    _record_change(
        transcript=transcript,
        actor=actor,
        action="manual_created",
        old_values={},
        new_values={
            "full_text": transcript.full_text,
            "segments": [_segment_snapshot(segment) for segment in created_segments],
            "revision": transcript.revision,
        },
    )
    return transcript


def _locked_transcript(transcript_id, expected_revision: int) -> Transcript:
    transcript = (
        Transcript.objects.select_for_update()
        .select_related("material__inspection")
        .get(pk=transcript_id)
    )
    if transcript.revision != expected_revision:
        raise RevisionConflict(transcript.revision)
    return transcript


@transaction.atomic
def edit_full_text(*, transcript_id, actor, expected_revision: int, full_text: str) -> Transcript:
    transcript = _locked_transcript(transcript_id, expected_revision)
    full_text = full_text.strip()
    if not full_text:
        raise ValueError("完整转写不能为空")
    if full_text == transcript.full_text:
        return transcript
    old = transcript.full_text
    transcript.full_text = full_text
    transcript.full_text_origin = Transcript.TextOrigin.MANUAL
    transcript.updated_by = actor
    transcript.revision += 1
    transcript.save(
        update_fields=(
            "full_text",
            "full_text_origin",
            "updated_by",
            "revision",
            "updated_at",
        )
    )
    _record_change(
        transcript=transcript,
        actor=actor,
        action="full_text_edited",
        old_values={"full_text": old},
        new_values={"full_text": full_text},
    )
    return transcript


@transaction.atomic
def edit_segment_text(
    *, transcript_id, segment_id, actor, expected_revision: int, text: str
) -> tuple[Transcript, TranscriptSegmentRecord]:
    transcript = _locked_transcript(transcript_id, expected_revision)
    segment = transcript.segments.select_for_update().get(pk=segment_id)
    text = text.strip()
    if not text:
        raise ValueError("转写片段不能为空")
    if segment.text == text:
        return transcript, segment
    old_snapshot = _segment_snapshot(segment)
    segment.text = text
    segment.text_origin = Transcript.TextOrigin.MANUAL
    segment.revision += 1
    segment.save(update_fields=("text", "text_origin", "revision"))
    transcript.updated_by = actor
    transcript.revision += 1
    transcript.save(update_fields=("updated_by", "revision", "updated_at"))
    _record_change(
        transcript=transcript,
        actor=actor,
        action="segment_text_edited",
        old_values={"segment": old_snapshot},
        new_values={"segment": _segment_snapshot(segment)},
    )
    return transcript, segment


@transaction.atomic
def edit_segment(
    *,
    transcript_id,
    segment_id,
    actor,
    expected_revision: int,
    text=_UNSET,
    confirmed_role=_UNSET,
) -> tuple[Transcript, TranscriptSegmentRecord]:
    """Atomically edit a segment's text and optional human-confirmed semantic role."""

    transcript = _locked_transcript(transcript_id, expected_revision)
    segment = transcript.segments.select_for_update().get(pk=segment_id)
    old_snapshot = _segment_snapshot(segment)
    changed = False

    if text is not _UNSET:
        normalized_text = str(text).strip()
        if not normalized_text:
            raise ValueError("转写片段不能为空")
        if normalized_text != segment.text:
            segment.text = normalized_text
            segment.text_origin = Transcript.TextOrigin.MANUAL
            changed = True

    if confirmed_role is not _UNSET:
        if confirmed_role is not None and not isinstance(confirmed_role, str):
            raise ValueError("人工确认角色必须是问、答或未确认")
        role = None if confirmed_role in {None, ""} else confirmed_role
        if role not in {
            None,
            SemanticRole.QUESTION.value,
            SemanticRole.ANSWER.value,
        }:
            raise ValueError("人工确认角色必须是问、答或未确认")
        if role != segment.confirmed_role:
            segment.confirmed_role = role
            if role is None:
                segment.role_confirmed_by = None
                segment.role_confirmed_at = None
            else:
                segment.role_confirmed_by = actor
                segment.role_confirmed_at = timezone.now()
            changed = True

    if not changed:
        return transcript, segment

    segment.revision += 1
    segment.save(
        update_fields=(
            "text",
            "text_origin",
            "confirmed_role",
            "role_confirmed_by",
            "role_confirmed_at",
            "revision",
        )
    )
    transcript.updated_by = actor
    transcript.revision += 1
    transcript.save(update_fields=("updated_by", "revision", "updated_at"))
    _record_change(
        transcript=transcript,
        actor=actor,
        action="segment_edited",
        old_values={"segment": old_snapshot},
        new_values={"segment": _segment_snapshot(segment)},
    )
    return transcript, segment


@transaction.atomic
def confirm_segment_role(
    *, transcript_id, segment_id, actor, expected_revision: int, role: str
) -> tuple[Transcript, TranscriptSegmentRecord]:
    if role not in {SemanticRole.QUESTION.value, SemanticRole.ANSWER.value}:
        raise ValueError("人工确认角色必须是问或答")
    transcript = _locked_transcript(transcript_id, expected_revision)
    segment = transcript.segments.select_for_update().get(pk=segment_id)
    if segment.confirmed_role == role:
        return transcript, segment
    old_snapshot = _segment_snapshot(segment)
    segment.confirmed_role = role
    segment.role_confirmed_by = actor
    segment.role_confirmed_at = timezone.now()
    segment.revision += 1
    segment.save(
        update_fields=(
            "confirmed_role",
            "role_confirmed_by",
            "role_confirmed_at",
            "revision",
        )
    )
    transcript.updated_by = actor
    transcript.revision += 1
    transcript.save(update_fields=("updated_by", "revision", "updated_at"))
    _record_change(
        transcript=transcript,
        actor=actor,
        action="segment_role_confirmed",
        old_values={"segment": old_snapshot},
        new_values={"segment": _segment_snapshot(segment)},
    )
    return transcript, segment
