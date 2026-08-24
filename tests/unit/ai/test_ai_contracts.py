from pathlib import Path
from uuid import uuid4

import pytest

from fire_inspect.ai.config import CapabilitySettings
from fire_inspect.ai.providers import (
    CapabilityUnavailable,
    DisabledProvider,
    FixedResponseProvider,
    ProviderFailure,
)
from fire_inspect.ai.schemas import (
    AIOutputError,
    DocumentDraftOutput,
    DocumentPhotoItemDraft,
    EvidenceReference,
    FactCandidate,
    FactExtractionOutput,
    TranscriptOutput,
    TranscriptSegment,
)


def test_disabled_provider_never_fabricates_success(tmp_path: Path) -> None:
    with pytest.raises(CapabilityUnavailable, match="人工处理"):
        DisabledProvider().transcribe(uuid4(), tmp_path / "missing.wav", 1_000)


def test_empty_asr_result_is_rejected() -> None:
    with pytest.raises(AIOutputError, match="完整转写为空"):
        TranscriptOutput("", (TranscriptSegment(uuid4(), 0, 1_000, "片段"),))


def test_cross_inspection_evidence_reference_is_rejected() -> None:
    allowed = uuid4()
    foreign = uuid4()
    output = FactExtractionOutput(
        (FactCandidate("候选事实", (EvidenceReference(foreign, 0, 1_000),)),)
    )
    with pytest.raises(AIOutputError, match="不属于当前检查"):
        output.validate_references({allowed: 2_000})


def test_final_enforcement_conclusion_is_rejected() -> None:
    with pytest.raises(AIOutputError, match="最终执法"):
        FactCandidate(
            "决定处以罚款。",
            (EvidenceReference(uuid4(), 0, 1_000),),
            is_final_enforcement_conclusion=True,
        )


def test_capability_settings_redacts_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIRE_INSPECT_ASR_PROVIDER", "http")
    monkeypatch.setenv("FIRE_INSPECT_ASR_ENDPOINT", "http://ai.invalid/asr")
    monkeypatch.setenv("FIRE_INSPECT_ASR_MODEL", "replaceable-model")
    monkeypatch.setenv("FIRE_INSPECT_ASR_API_KEY", "SECRET-CANARY")
    settings = CapabilitySettings.from_environment("asr")
    assert "SECRET-CANARY" not in repr(settings)
    assert "redacted" in repr(settings)


def test_fixed_provider_failures_are_local_and_retryable(tmp_path: Path) -> None:
    provider = FixedResponseProvider(fail_capabilities=frozenset({"vision"}))
    with pytest.raises(ProviderFailure, match="单独重试"):
        provider.describe(uuid4(), [(0, tmp_path / "frame.jpg")])
    # A failure in vision must not disable another capability.
    assert provider.embed(["仍可检索"])[0].values


def test_document_draft_schema_rejects_empty_or_duplicate_source_candidates() -> None:
    with pytest.raises(AIOutputError, match="空结果"):
        DocumentDraftOutput(document_type="record", fields={})
    source_id = uuid4()
    with pytest.raises(AIOutputError, match="重复照片"):
        DocumentDraftOutput(
            document_type="photo",
            fields={"shooting_location": "现场"},
            photo_items=(
                DocumentPhotoItemDraft(source_id, "反映内容一"),
                DocumentPhotoItemDraft(source_id, "反映内容二"),
            ),
        )


def test_fixed_document_draft_uses_only_supplied_confirmed_sources() -> None:
    fact_id = uuid4()
    output = FixedResponseProvider().draft_document(
        "record",
        {
            "inspection": {
                "unit_name": "测试单位",
                "unit_address": "测试地址",
                "inspection_at": "2026-08-24T10:30:00+08:00",
                "personnel": [],
            },
            "facts": [{"id": str(fact_id), "content": "大厅存在堆放物品。", "revision": 3}],
            "photo_items": [],
            "confirmed_qa_items": [],
            "confirmed_transcript_segments": [],
        },
    )
    assert output.fields["inspection_content"] == "大厅存在堆放物品。"
    assert output.referenced_fact_ids == (fact_id,)
