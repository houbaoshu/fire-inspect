from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from uuid import UUID, uuid4


class FactReviewState(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    WITHDRAWN = "withdrawn"


class FactOrigin(StrEnum):
    AI = "ai"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    material_id: UUID
    start_ms: int
    end_ms: int
    note: str = ""

    def validate(self, allowed_materials: dict[UUID, int]) -> None:
        duration = allowed_materials.get(self.material_id)
        if duration is None:
            raise ValueError("证据材料不属于当前检查")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValueError("证据时间范围无效")
        if self.end_ms > duration + 3_000:
            raise ValueError("证据时间超过材料时间轴")


@dataclass(frozen=True, slots=True)
class FactHistory:
    actor_id: UUID
    occurred_at: datetime
    action: str
    old_value: dict[str, object]
    new_value: dict[str, object]


class Fact:
    def __init__(
        self,
        inspection_id: UUID,
        content: str,
        *,
        origin: FactOrigin,
        created_by: UUID,
        needs_verification: bool = False,
        verification_note: str = "",
        evidence: tuple[EvidenceLink, ...] = (),
        id: UUID | None = None,
    ) -> None:
        if not content.strip():
            raise ValueError("事实内容不能为空")
        if origin is FactOrigin.AI and not evidence and not needs_verification:
            raise ValueError("无可靠证据的 AI 事实必须标记待核实")
        self.id = id or uuid4()
        self.inspection_id = inspection_id
        self.content = content
        self.origin = origin
        self.created_by = created_by
        self.needs_verification = needs_verification
        self.verification_note = verification_note
        self.evidence = list(evidence)
        self.review_state = FactReviewState.CANDIDATE
        self.revision = 1
        self.confirmed_by: UUID | None = None
        self.confirmed_at: datetime | None = None
        self.history: list[FactHistory] = []

    def snapshot(self) -> dict[str, object]:
        return {
            "content": self.content,
            "review_state": self.review_state.value,
            "needs_verification": self.needs_verification,
            "verification_note": self.verification_note,
            "evidence": [
                {
                    "material_id": str(item.material_id),
                    "start_ms": item.start_ms,
                    "end_ms": item.end_ms,
                    "note": item.note,
                }
                for item in self.evidence
            ],
            "revision": self.revision,
        }

    def _change(self, actor_id: UUID, action: str, operation) -> None:
        before = self.snapshot()
        operation()
        after = self.snapshot()
        if before == after:
            return
        self.history.append(FactHistory(actor_id, datetime.now(UTC), action, before, after))

    def confirm(self, actor_id: UUID) -> None:
        if self.review_state is FactReviewState.CONFIRMED:
            return
        if self.review_state is FactReviewState.WITHDRAWN:
            raise ValueError("已撤回事实需先恢复为候选")

        def operation() -> None:
            self.review_state = FactReviewState.CONFIRMED
            self.confirmed_by = actor_id
            self.confirmed_at = datetime.now(UTC)
            self.revision += 1

        self._change(actor_id, "confirm", operation)

    def withdraw(self, actor_id: UUID) -> None:
        if self.review_state is FactReviewState.WITHDRAWN:
            return

        def operation() -> None:
            self.review_state = FactReviewState.WITHDRAWN
            self.revision += 1

        self._change(actor_id, "withdraw", operation)

    def restore_candidate(self, actor_id: UUID) -> None:
        if self.review_state is not FactReviewState.WITHDRAWN:
            raise ValueError("只有已撤回事实可以恢复")

        def operation() -> None:
            self.review_state = FactReviewState.CANDIDATE
            self.confirmed_by = None
            self.confirmed_at = None
            self.revision += 1

        self._change(actor_id, "restore", operation)

    def edit(
        self,
        actor_id: UUID,
        *,
        content: str,
        needs_verification: bool | None = None,
        verification_note: str | None = None,
    ) -> None:
        if not content.strip():
            raise ValueError("事实内容不能为空")

        def operation() -> None:
            self.content = content
            if needs_verification is not None:
                self.needs_verification = needs_verification
            if verification_note is not None:
                self.verification_note = verification_note
            self.revision += 1

        self._change(actor_id, "edit", operation)

    def replace_evidence(
        self,
        actor_id: UUID,
        evidence: tuple[EvidenceLink, ...],
        *,
        allowed_materials: dict[UUID, int],
    ) -> None:
        for item in evidence:
            item.validate(allowed_materials)

        def operation() -> None:
            self.evidence = list(evidence)
            if not evidence:
                self.needs_verification = True
            self.revision += 1

        self._change(actor_id, "replace_evidence", operation)


def fact_idempotency_key(material_id: UUID, input_revision: int, content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).lower()
    normalized = re.sub(r"\s+", "", normalized)
    return sha256(f"{material_id}:{input_revision}:{normalized}".encode()).hexdigest()
