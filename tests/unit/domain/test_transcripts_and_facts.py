from datetime import UTC, datetime
from uuid import uuid4

import pytest

from fire_inspect.ai.schemas import SemanticRole, TranscriptOutput, TranscriptSegment
from fire_inspect.facts import (
    EvidenceLink,
    Fact,
    FactOrigin,
    FactReviewState,
    fact_idempotency_key,
)
from fire_inspect.transcripts import RevisionConflict, TranscriptAggregate


def test_transcript_text_and_roles_are_editable_with_full_history() -> None:
    material_id = uuid4()
    actor_id = uuid4()
    aggregate = TranscriptAggregate.from_ai(
        TranscriptOutput(
            "候选全文",
            (
                TranscriptSegment(material_id, 0, 1_000, "候选问句", SemanticRole.QUESTION),
                TranscriptSegment(material_id, 1_000, 2_000, "候选答句", SemanticRole.UNKNOWN),
            ),
        )
    )
    occurred_at = datetime(2026, 8, 24, tzinfo=UTC)
    aggregate.edit_segment_text(
        aggregate.segments[1].id,
        "人工修正答句",
        actor_id=actor_id,
        expected_revision=1,
        occurred_at=occurred_at,
    )
    aggregate.confirm_role(
        aggregate.segments[1].id,
        SemanticRole.ANSWER,
        actor_id=actor_id,
        expected_revision=2,
        occurred_at=occurred_at,
    )
    assert aggregate.segments[1].text == "人工修正答句"
    assert aggregate.segments[1].confirmed_role is SemanticRole.ANSWER
    assert [(item.field, item.actor_id) for item in aggregate.history] == [
        ("text", actor_id),
        ("confirmed_role", actor_id),
    ]
    assert all(item.occurred_at == occurred_at for item in aggregate.history)


def test_transcript_rejects_stale_revision() -> None:
    material_id = uuid4()
    aggregate = TranscriptAggregate.from_ai(
        TranscriptOutput("全文", (TranscriptSegment(material_id, 0, 1_000, "片段"),))
    )
    with pytest.raises(RevisionConflict):
        aggregate.edit_full_text("修改", actor_id=uuid4(), expected_revision=99)


def test_fact_review_state_and_evidence_history() -> None:
    actor_id = uuid4()
    material_id = uuid4()
    fact = Fact(
        uuid4(),
        "候选事实",
        origin=FactOrigin.AI,
        created_by=actor_id,
        evidence=(EvidenceLink(material_id, 1_000, 2_000),),
    )
    fact.confirm(actor_id)
    assert fact.review_state is FactReviewState.CONFIRMED
    fact.withdraw(actor_id)
    assert fact.review_state is FactReviewState.WITHDRAWN
    fact.restore_candidate(actor_id)
    assert fact.review_state is FactReviewState.CANDIDATE
    assert [event.action for event in fact.history] == ["confirm", "withdraw", "restore"]


def test_cross_inspection_or_out_of_range_evidence_is_rejected() -> None:
    material_id = uuid4()
    foreign = uuid4()
    with pytest.raises(ValueError, match="不属于"):
        EvidenceLink(foreign, 0, 500).validate({material_id: 2_000})
    with pytest.raises(ValueError, match="时间轴"):
        EvidenceLink(material_id, 0, 6_000).validate({material_id: 2_000})


def test_ai_fact_without_evidence_is_visibly_unverified() -> None:
    with pytest.raises(ValueError, match="待核实"):
        Fact(uuid4(), "无来源事实", origin=FactOrigin.AI, created_by=uuid4())


def test_fact_retry_idempotency_normalizes_whitespace() -> None:
    material_id = uuid4()
    assert fact_idempotency_key(material_id, 2, "大厅 存在堆物") == fact_idempotency_key(
        material_id, 2, "大厅存在堆物"
    )
