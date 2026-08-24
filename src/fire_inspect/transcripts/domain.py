from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from fire_inspect.ai.schemas import SemanticRole, TranscriptOutput


class RevisionConflict(RuntimeError):
    pass


class TextOrigin(StrEnum):
    AI = "ai"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class SegmentRecord:
    id: UUID
    sequence: int
    start_ms: int
    end_ms: int
    text: str
    suggested_role: SemanticRole
    confirmed_role: SemanticRole | None = None
    text_origin: TextOrigin = TextOrigin.AI
    role_confirmed_by: UUID | None = None
    role_confirmed_at: datetime | None = None
    revision: int = 1


@dataclass(frozen=True, slots=True)
class ChangeRecord:
    actor_id: UUID
    occurred_at: datetime
    object_type: str
    object_id: UUID
    field: str
    old_value: object
    new_value: object


class TranscriptAggregate:
    def __init__(
        self,
        material_id: UUID,
        full_text: str,
        segments: list[SegmentRecord],
        *,
        id: UUID | None = None,
        revision: int = 1,
    ) -> None:
        if not full_text.strip():
            raise ValueError("完整转写不能为空")
        if not segments:
            raise ValueError("转写必须包含时间锚片段")
        if [item.sequence for item in segments] != list(range(1, len(segments) + 1)):
            raise ValueError("转写片段顺序必须连续")
        self.id = id or uuid4()
        self.material_id = material_id
        self.full_text = full_text
        self.segments = list(segments)
        self.revision = revision
        self.history: list[ChangeRecord] = []

    @classmethod
    def from_ai(cls, output: TranscriptOutput) -> TranscriptAggregate:
        return cls(
            material_id=output.material_id,
            full_text=output.full_text,
            segments=[
                SegmentRecord(
                    id=uuid4(),
                    sequence=index,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=segment.text,
                    suggested_role=segment.suggested_role,
                )
                for index, segment in enumerate(output.segments, start=1)
            ],
        )

    def _expect(self, expected_revision: int) -> None:
        if expected_revision != self.revision:
            raise RevisionConflict("转写已被其他操作修改，请刷新后重试")

    def _record(
        self,
        actor_id: UUID,
        object_type: str,
        object_id: UUID,
        field: str,
        old_value: object,
        new_value: object,
        occurred_at: datetime | None,
    ) -> None:
        self.history.append(
            ChangeRecord(
                actor_id=actor_id,
                occurred_at=occurred_at or datetime.now(UTC),
                object_type=object_type,
                object_id=object_id,
                field=field,
                old_value=old_value,
                new_value=new_value,
            )
        )

    def edit_full_text(
        self,
        text: str,
        *,
        actor_id: UUID,
        expected_revision: int,
        occurred_at: datetime | None = None,
    ) -> None:
        self._expect(expected_revision)
        if not text.strip():
            raise ValueError("完整转写不能为空")
        if text == self.full_text:
            return
        old_value = self.full_text
        self.full_text = text
        self.revision += 1
        self._record(actor_id, "transcript", self.id, "full_text", old_value, text, occurred_at)

    def edit_segment_text(
        self,
        segment_id: UUID,
        text: str,
        *,
        actor_id: UUID,
        expected_revision: int,
        occurred_at: datetime | None = None,
    ) -> SegmentRecord:
        self._expect(expected_revision)
        if not text.strip():
            raise ValueError("转写片段不能为空")
        index, segment = self._locate(segment_id)
        if text == segment.text:
            return segment
        updated = replace(
            segment,
            text=text,
            text_origin=TextOrigin.MANUAL,
            revision=segment.revision + 1,
        )
        self.segments[index] = updated
        self.revision += 1
        self._record(
            actor_id, "transcript_segment", segment.id, "text", segment.text, text, occurred_at
        )
        return updated

    def confirm_role(
        self,
        segment_id: UUID,
        role: SemanticRole,
        *,
        actor_id: UUID,
        expected_revision: int,
        occurred_at: datetime | None = None,
    ) -> SegmentRecord:
        self._expect(expected_revision)
        if role is SemanticRole.UNKNOWN:
            raise ValueError("人工确认角色必须是问或答")
        index, segment = self._locate(segment_id)
        if segment.confirmed_role is role:
            return segment
        event_time = occurred_at or datetime.now(UTC)
        updated = replace(
            segment,
            confirmed_role=role,
            role_confirmed_by=actor_id,
            role_confirmed_at=event_time,
            revision=segment.revision + 1,
        )
        self.segments[index] = updated
        self.revision += 1
        self._record(
            actor_id,
            "transcript_segment",
            segment.id,
            "confirmed_role",
            segment.confirmed_role,
            role,
            event_time,
        )
        return updated

    def _locate(self, segment_id: UUID) -> tuple[int, SegmentRecord]:
        for index, segment in enumerate(self.segments):
            if segment.id == segment_id:
                return index, segment
        raise KeyError("转写片段不存在")
