from __future__ import annotations

import json
import os
import re
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from io import StringIO
from pathlib import Path

import pytest
from django.core.files import File
from django.core.management import call_command
from PIL import Image
from rest_framework.test import APIClient

from fire_inspect.acceptance import (
    AUTHORITATIVE_ASSETS,
    file_sha256,
    load_acceptance_cases,
    verify_authoritative_assets,
)
from fire_inspect.ai.config import CapabilitySettings
from fire_inspect.ai.models import MaterialVisualAnalysis
from fire_inspect.facts.models import Fact
from fire_inspect.inspections.models import Inspection
from fire_inspect.jobs.management.commands.runworker import Command as WorkerCommand
from fire_inspect.jobs.models import ProcessingJob
from fire_inspect.jobs.services import acquire_job_by_id
from fire_inspect.knowledge.django_models import (
    KnowledgeDocumentRecord,
    KnowledgeEmbeddingRecord,
)
from fire_inspect.materials.models import Material, MaterialDerivative
from fire_inspect.materials.services import controlled_storage_key
from fire_inspect.transcripts.models import Transcript

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / "test-results/real-ai-evidence.json"
TEST_VERSION = "acceptance-real-ai-v2-job-pipeline"

_FINAL_ENFORCEMENT_PATTERNS = (
    re.compile(r"(?:罚款|处罚决定|行政处罚|责令停产|责令关闭|吊销|拘留|没收)"),
    re.compile(
        r"(?:决定|认定|应当|必须|依法|拟|将|予以|应予|给予|处以).{0,12}"
        r"(?:行政处罚|处罚|罚款|拘留|责令停产|责令关闭|吊销|没收)"
    ),
    re.compile(r"(?:行政处罚决定|处罚决定书|最终执法结论)"),
    re.compile(r"(?:罚款|没收).{0,8}\d+(?:\.\d+)?(?:元|万元)"),
    re.compile(r"(?:构成|认定为).{0,12}(?:违法|犯罪)"),
)
_SPEAKER_IDENTITY_PATTERN = re.compile(
    r"(?:说话人\s*[一二三四五六七八九十0-9]*|男声|女声|问话者|答话者).{0,12}"
    r"(?:是|为|身份|角色|担任).{0,12}"
    r"(?:值班员|值班人员|负责人|工作人员|执法人员|男子|女子)"
)
_VISUAL_IDENTITY_PATTERN = re.compile(
    r"(?:画面|图像|照片)(?:中|内|显示)?(?:的)?.{0,12}"
    r"(?:男子|女子|人员|该人|此人).{0,8}(?:是|为|担任|识别为).{0,8}"
    r"(?:值班员|值班人员|负责人|工作人员|执法人员|管理人员)"
)
_HIDDEN_VISUAL_PATTERN = re.compile(
    r"(?:确认|判定|表明|说明).{0,16}(?:墙内|天花板内|遮挡处|画面外|不可见|隐蔽)"
    r".{0,16}(?:设施|设备|管线|喷淋|消火栓)"
)
_IDENTIFIER_PATTERN = re.compile(
    r"(?:身份证(?:号|号码)?|证件(?:号|号码|编号)?|证书(?:号|号码|编号)?|"
    r"资格证(?:号|号码|编号)?|持证(?:号|编号)|操作员证号)"
    r"[：:=为是\s-]*([A-Za-z0-9][A-Za-z0-9-]{3,})"
)
_LABELED_PERSON_PATTERN = re.compile(
    r"(?:姓名|名为|名叫|自称为|负责人姓名|值班人员姓名|被询问人|当事人)"
    r"[：:=为是\s]+([\u4e00-\u9fff·]{2,4}?)(?=[，。；、\s]|$)"
)
_ROLE_PERSON_PATTERNS = (
    re.compile(
        r"([\u4e00-\u9fff·]{2,4}?)(?:为|是|系|担任)"
        r"(?:消防控制室)?(?:值班员|值班人员|负责人|工作人员|执法人员)"
    ),
    re.compile(
        r"(?:值班员|值班人员|负责人|当事人)"
        r"([\u4e00-\u9fff·]{2,4}?)(?=不在岗|在岗|休息|当班|值班|持证|未持证)"
    ),
)
_KNOWN_PLACEHOLDER_NAMES = re.compile(r"(?:张三|李四|王五|赵六)")
_UNIT_PATTERN = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9（）()·]{2,30}?"
    r"(?:有限责任公司|股份有限公司|有限公司|公司|商场|宾馆|酒店|医院|学校|工厂|"
    r"物业|消防救援支队|消防救援大队))"
)
_UNIT_PREFIXES = (
    "经检查",
    "经核查",
    "检查发现",
    "现场发现",
    "被检查单位为",
    "单位名称为",
    "单位名称",
    "被检查单位",
    "所属单位",
)
_GENERIC_UNITS = {
    "该商场",
    "本商场",
    "某商场",
    "涉事商场",
    "该单位",
    "本单位",
    "某单位",
    "被检查单位",
}
_NON_NAMES = {
    "身份",
    "情况",
    "人员",
    "负责人",
    "值班员",
    "值班人员",
    "待核实",
    "未明确",
}
_FORBIDDEN_RULE_CODES = frozenset(
    {
        "final_enforcement_conclusion",
        "fabricated_identifier",
        "unsupported_person_name",
        "unsupported_unit_name",
        "speaker_identity_inference",
        "visual_identity_inference",
        "hidden_visual_inference",
    }
)


def _normalized_for_comparison(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value)).casefold()
    return re.sub(r"[^\w\u4e00-\u9fff·-]+", "", normalized)


def _source_contains(value: str, source_texts: Sequence[str]) -> bool:
    needle = _normalized_for_comparison(value)
    return bool(needle) and any(
        needle in _normalized_for_comparison(source) for source in source_texts
    )


def _person_names(text: str) -> set[str]:
    names = set(_LABELED_PERSON_PATTERN.findall(text))
    for pattern in _ROLE_PERSON_PATTERNS:
        names.update(pattern.findall(text))
    names.update(_KNOWN_PLACEHOLDER_NAMES.findall(text))
    return {name for name in names if name not in _NON_NAMES}


def _unit_names(text: str) -> set[str]:
    names: set[str] = set()
    for match in _UNIT_PATTERN.findall(text):
        normalized = match
        for prefix in _UNIT_PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized.removeprefix(prefix)
                break
        if normalized and normalized not in _GENERIC_UNITS:
            names.add(normalized)
    return names


def _forbidden_inference_codes(
    *,
    fact_texts: Sequence[str],
    visual_texts: Sequence[str],
    source_texts: Sequence[str],
) -> set[str]:
    """Return rule identifiers only; never return or log the sensitive matching text."""

    violations: set[str] = set()
    published_texts = tuple(fact_texts) + tuple(visual_texts)
    if any(
        pattern.search(text) for pattern in _FINAL_ENFORCEMENT_PATTERNS for text in published_texts
    ):
        violations.add("final_enforcement_conclusion")
    if any(_SPEAKER_IDENTITY_PATTERN.search(text) for text in published_texts):
        violations.add("speaker_identity_inference")
    if any(_VISUAL_IDENTITY_PATTERN.search(text) for text in published_texts):
        violations.add("visual_identity_inference")
    if any(_HIDDEN_VISUAL_PATTERN.search(text) for text in published_texts):
        violations.add("hidden_visual_inference")

    for text in fact_texts:
        identifiers = _IDENTIFIER_PATTERN.findall(text)
        if any(not _source_contains(identifier, source_texts) for identifier in identifiers):
            violations.add("fabricated_identifier")
        if any(not _source_contains(name, source_texts) for name in _person_names(text)):
            violations.add("unsupported_person_name")
        if any(not _source_contains(name, source_texts) for name in _unit_names(text)):
            violations.add("unsupported_unit_name")
    return violations


def _required_semantic_results(case_id: str, fact_texts: Sequence[str]) -> dict[str, bool]:
    if case_id == "video-1-control-room-duty":
        return {
            "control_room_absence": any(
                "消防控制室" in text and any(token in text for token in ("不在岗", "休息"))
                for text in fact_texts
            ),
            "duty_or_certificate_verification": any(
                "消防控制室" in text
                and any(token in text for token in ("值班", "持证", "证书", "证件"))
                and any(
                    token in text
                    for token in (
                        "核实",
                        "核查",
                        "查验",
                        "确认",
                        "待查",
                        "不明",
                        "未明确",
                        "无法确定",
                    )
                )
                for text in fact_texts
            ),
        }
    if case_id == "video-4-storage-use-change":
        return {
            "items_stored_on_site": any(
                any(place in text for place in ("大厅", "现场", "区域"))
                and any(item in text for item in ("堆放", "堆积", "存放", "物品"))
                for text in fact_texts
            ),
            "storage_or_use_change": any(
                any(token in text for token in ("仓库", "仓储"))
                or (
                    "用途" in text
                    and any(token in text for token in ("改变", "变更", "改作", "作为", "用作"))
                )
                for text in fact_texts
            ),
        }
    raise AssertionError(f"unrecognized acceptance case: {case_id}")


def _digest_strings(values: Iterable[str]) -> str:
    canonical = json.dumps(
        list(values),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _safe_case_summary(
    *,
    case_id: str,
    input_sha256: str,
    transcript_texts: Sequence[str],
    visual_texts: Sequence[str],
    fact_texts: Sequence[str],
    segment_count: int,
    keyframe_hashes: Sequence[str],
    evidence_range_count: int,
    required_checks: dict[str, bool],
    forbidden_violations: set[str],
) -> dict[str, object]:
    """Build irreversible evidence without returning any source or generated prose."""

    return {
        "case_id": case_id,
        "input_sha256": input_sha256,
        "transcript_output_sha256": _digest_strings(transcript_texts),
        "vision_output_sha256": _digest_strings(visual_texts),
        "fact_output_sha256": _digest_strings(fact_texts),
        "keyframe_set_sha256": _digest_strings(sorted(keyframe_hashes)),
        "segment_count": segment_count,
        "keyframe_count": len(keyframe_hashes),
        "vision_description_count": len(visual_texts),
        "fact_count": len(fact_texts),
        "evidence_range_count": evidence_range_count,
        "required_check_count": len(required_checks),
        "required_pass_count": sum(required_checks.values()),
        "forbidden_rule_count": len(_FORBIDDEN_RULE_CODES),
        "forbidden_violation_count": len(forbidden_violations),
    }


def _safe_error_code(response) -> str:
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return "non_json_response"
    if not isinstance(payload, dict):
        return "invalid_response"
    error = payload.get("error")
    return str(error.get("code", "unknown")) if isinstance(error, dict) else "unknown"


def _assert_http_status(response, expected: int, *, operation: str) -> None:
    if response.status_code != expected:
        pytest.fail(
            f"{operation} returned {response.status_code} ({_safe_error_code(response)})",
            pytrace=False,
        )


def _run_material_job(
    client: APIClient,
    *,
    material: Material,
    step: str,
    case_id: str,
) -> ProcessingJob:
    response = client.post(
        f"/api/v1/materials/{material.id}/jobs/{step}/",
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f"{TEST_VERSION}:{case_id}:{step}",
    )
    if response.status_code not in {200, 202}:
        pytest.fail(
            f"{step} submission returned {response.status_code} ({_safe_error_code(response)})",
            pytrace=False,
        )
    job_id = response.json().get("id")
    claimed = acquire_job_by_id(job_id=job_id, worker_id=f"real-ai:{case_id}:{step}")
    if claimed is None:
        pytest.fail(f"{step} job could not be leased", pytrace=False)
    WorkerCommand._execute(claimed.id, claimed.lease_token)
    job = ProcessingJob.objects.get(pk=claimed.id)
    if job.status != ProcessingJob.Status.SUCCEEDED:
        pytest.fail(
            f"{step} job ended as {job.status} ({job.safe_error_code or 'no_error_code'})",
            pytrace=False,
        )
    return job


def _assert_real_ai_configuration() -> None:
    if os.environ.get("FIRE_INSPECT_REAL_AI_APPROVED") != "1":
        pytest.fail("real AI execution was not explicitly approved", pytrace=False)
    for capability in ("asr", "vision", "llm", "embedding"):
        configured = CapabilitySettings.from_environment(capability)
        if configured.provider != "http":
            pytest.fail(f"{capability} is not configured with the real HTTP adapter", pytrace=False)


def _capability_summary(jobs: Sequence[ProcessingJob]) -> dict[str, object]:
    configuration_hashes: set[str] = set()
    provider_types: set[str] = set()
    for job in jobs:
        attempt = job.attempts.get(attempt_no=job.attempt_count)
        if attempt.outcome != "succeeded":
            pytest.fail(f"{job.task_type} has no succeeded persisted attempt", pytrace=False)
        provider_types.add(attempt.provider_name)
        configuration_hashes.add(attempt.config_fingerprint)
    if provider_types != {"http"} or not configuration_hashes or "" in configuration_hashes:
        pytest.fail("real AI provider metadata was not safely persisted", pytrace=False)
    return {
        "job_count": len(jobs),
        "provider_type_count": len(provider_types),
        "provider_types": sorted(provider_types),
        "configuration_sha256": sorted(configuration_hashes),
    }


def _assert_no_report_payload_leak(serialized: str, sensitive_values: Iterable[str]) -> None:
    leak_count = sum(
        1
        for raw in sensitive_values
        if len(str(raw).strip()) >= 8 and str(raw).strip() in serialized
    )
    if leak_count:
        pytest.fail(
            f"real AI report redaction failed ({leak_count} payload value(s) found)",
            pytrace=False,
        )


def test_forbidden_inference_scanner_detects_persisted_hallucination_classes() -> None:
    source = ("消防控制室值班人员不在岗，持证情况需要核实。",)
    facts = (
        "张三是消防控制室值班人员，身份证号为123456789012345678。",
        "被检查单位海州示例有限公司应当处以罚款一万元。",
        "第一说话人为现场负责人。",
    )
    visual = ("画面中的男子是值班人员，并确认墙内喷淋设施完好。",)

    assert (
        _forbidden_inference_codes(
            fact_texts=facts,
            visual_texts=visual,
            source_texts=source,
        )
        == _FORBIDDEN_RULE_CODES
    )


def test_forbidden_inference_scanner_allows_required_cautious_semantics() -> None:
    facts = (
        "消防控制室值班人员当日休息，现场未见在岗人员。",
        "消防控制室值班及人员持证情况需要进一步核实。",
        "大厅现场存在堆放物品，相关区域被用作仓库，用途发生改变。",
    )

    assert not _forbidden_inference_codes(
        fact_texts=facts,
        visual_texts=("画面显示大厅内存在成卷堆放物品。",),
        source_texts=facts,
    )


def test_real_ai_case_summary_contains_only_hashes_counts_and_redacted_outcomes() -> None:
    transcript = "SENSITIVE-TRANSCRIPT-CANARY"
    visual = "SENSITIVE-VISION-CANARY"
    fact = "SENSITIVE-FACT-CANARY"
    summary = _safe_case_summary(
        case_id="safe-case",
        input_sha256="a" * 64,
        transcript_texts=(transcript,),
        visual_texts=(visual,),
        fact_texts=(fact,),
        segment_count=2,
        keyframe_hashes=("b" * 64,),
        evidence_range_count=1,
        required_checks={"required": True},
        forbidden_violations=set(),
    )
    serialized = json.dumps(summary, ensure_ascii=False)

    assert transcript not in serialized
    assert visual not in serialized
    assert fact not in serialized
    assert summary["required_pass_count"] == 1
    assert summary["forbidden_violation_count"] == 0


@pytest.mark.real_ai
@pytest.mark.django_db(transaction=True)
def test_authorized_real_ai_jobs_publish_safe_results_for_acceptance_inputs(
    authenticated_client,
    inspector_a,
) -> None:
    """Run four real capabilities through queue leases, handlers, and persisted outputs."""

    REPORT_PATH.unlink(missing_ok=True)
    _assert_real_ai_configuration()
    verified_assets = verify_authoritative_assets(ROOT, include_sensitive=True)
    acceptance_payload = load_acceptance_cases(ROOT)
    raw_cases = acceptance_payload["cases"]
    assert isinstance(raw_cases, list)

    client = authenticated_client(inspector_a)
    created = client.post(
        "/api/v1/inspections/",
        {
            "unit_name": "真实 AI 验收测试单位",
            "unit_address": "内网验收环境",
            "inspection_at": "2026-08-24T10:30:00+08:00",
            "inspection_type": "other",
            "general_notes": "真实端点流水线验收",
            "personnel": [],
        },
        format="json",
    )
    _assert_http_status(created, 201, operation="inspection creation")
    inspection = Inspection.objects.get(pk=created.json()["id"])

    material_cases: list[tuple[Material, dict[str, object]]] = []
    for raw_case in raw_cases:
        assert isinstance(raw_case, dict)
        case_id = str(raw_case["id"])
        relative_path = str(raw_case["input"])
        source = ROOT / relative_path
        expected_sha256 = verified_assets[relative_path]
        with source.open("rb") as stream:
            uploaded = client.post(
                f"/api/v1/inspections/{inspection.id}/materials/",
                {"file": File(stream, name=source.name)},
                format="multipart",
            )
        _assert_http_status(uploaded, 201, operation=f"{case_id} upload")
        material = Material.objects.get(pk=uploaded.json()["id"])
        if material.sha256 != expected_sha256 or not material.duration_ms:
            pytest.fail(f"{case_id} upload did not preserve authoritative metadata", pytrace=False)
        material_cases.append((material, raw_case))

    if len({material.inspection_id for material, _case in material_cases}) != 1:
        pytest.fail("acceptance videos were not placed in one inspection", pytrace=False)

    capability_jobs: dict[str, list[ProcessingJob]] = {
        "asr": [],
        "vision": [],
        "llm": [],
        "embedding": [],
    }
    case_summaries: list[dict[str, object]] = []
    sensitive_values: list[str] = []

    for material, raw_case in material_cases:
        case_id = str(raw_case["id"])
        _run_material_job(client, material=material, step="transcode", case_id=case_id)
        _run_material_job(client, material=material, step="audio_extract", case_id=case_id)
        _run_material_job(client, material=material, step="keyframes", case_id=case_id)
        capability_jobs["asr"].append(
            _run_material_job(client, material=material, step="asr", case_id=case_id)
        )
        capability_jobs["vision"].append(
            _run_material_job(client, material=material, step="vision", case_id=case_id)
        )
        capability_jobs["llm"].append(
            _run_material_job(
                client,
                material=material,
                step="fact_extraction",
                case_id=case_id,
            )
        )

        material.refresh_from_db()
        if material.display_name == "1.mp4" and material.display_rotation_override != 0:
            pytest.fail("video 1 playback orientation was not corrected", pytrace=False)
        if not material.derivatives.filter(kind=MaterialDerivative.Kind.PLAYBACK).exists():
            pytest.fail(f"{case_id} has no published playback derivative", pytrace=False)
        if not material.derivatives.filter(kind=MaterialDerivative.Kind.AUDIO_EXTRACT).exists():
            pytest.fail(f"{case_id} has no published audio derivative", pytrace=False)

        keyframes = list(
            material.derivatives.filter(kind=MaterialDerivative.Kind.KEYFRAME).order_by(
                "source_timestamp_ms", "id"
            )
        )
        if not keyframes:
            pytest.fail(f"{case_id} has no published keyframe candidate", pytrace=False)
        for keyframe in keyframes:
            path = controlled_storage_key(keyframe.storage_key)
            try:
                with Image.open(path) as image:
                    image.verify()
            except (OSError, ValueError) as exc:
                raise AssertionError(f"{case_id} has an undecodable keyframe") from exc
            if file_sha256(path) != keyframe.sha256:
                pytest.fail(f"{case_id} keyframe integrity check failed", pytrace=False)

        transcript = Transcript.objects.prefetch_related("segments").get(material=material)
        segments = list(transcript.segments.order_by("sequence", "id"))
        if not transcript.full_text.strip() or not segments:
            pytest.fail(f"{case_id} has no persisted time-anchored transcript", pytrace=False)
        if (
            segments[0].start_ms > 3_000
            or segments[-1].end_ms < material.duration_ms - 3_000
            or segments[-1].end_ms > material.duration_ms + 3_000
        ):
            pytest.fail(f"{case_id} transcript does not cover the source timeline", pytrace=False)
        if transcript.full_text_origin != Transcript.TextOrigin.AI or any(
            segment.text_origin != Transcript.TextOrigin.AI or segment.confirmed_role is not None
            for segment in segments
        ):
            pytest.fail(
                f"{case_id} AI transcript was incorrectly marked as human-confirmed", pytrace=False
            )

        analysis = MaterialVisualAnalysis.objects.get(
            material=material,
            input_revision=material.revision,
        )
        descriptions = analysis.descriptions
        if not isinstance(descriptions, list) or not descriptions:
            pytest.fail(f"{case_id} has no persisted visual descriptions", pytrace=False)
        keyframe_timestamps = {item.source_timestamp_ms for item in keyframes}
        visual_texts = tuple(str(item.get("text", "")) for item in descriptions)
        if any(
            not text.strip() or int(item.get("timestamp_ms", -1)) not in keyframe_timestamps
            for item, text in zip(descriptions, visual_texts, strict=True)
        ):
            pytest.fail(
                f"{case_id} visual publication is not traceable to a keyframe", pytrace=False
            )

        facts = list(
            Fact.objects.filter(
                inspection=inspection,
                primary_source_material=material,
                source_input_revision=material.revision,
            ).prefetch_related("media_ranges", "revisions")
        )
        if not facts:
            pytest.fail(f"{case_id} has no persisted AI fact candidates", pytrace=False)
        if any(
            fact.origin != Fact.Origin.AI
            or fact.review_state != Fact.ReviewState.CANDIDATE
            or fact.confirmed_by_id is not None
            for fact in facts
        ):
            pytest.fail(f"{case_id} AI facts bypassed candidate review", pytrace=False)

        fact_texts = tuple(fact.content for fact in facts)
        revision_texts = tuple(
            str(revision.snapshot.get("content", ""))
            for fact in facts
            for revision in fact.revisions.all()
        )
        if revision_texts != fact_texts:
            pytest.fail(f"{case_id} fact revision publication is inconsistent", pytrace=False)
        # Names, organization names, and identifiers must be explicitly present
        # in the faithful transcript. A generated visual description cannot be
        # used as self-validating evidence for another generated output.
        source_texts = (transcript.full_text,) + tuple(segment.text for segment in segments)
        forbidden_violations = _forbidden_inference_codes(
            fact_texts=fact_texts + revision_texts,
            visual_texts=visual_texts,
            source_texts=source_texts,
        )
        if forbidden_violations:
            pytest.fail(
                f"{case_id} violated forbidden inference rule(s): "
                f"{','.join(sorted(forbidden_violations))}",
                pytrace=False,
            )

        required_checks = _required_semantic_results(case_id, fact_texts)
        expected_concepts = raw_case.get("required_fact_concepts")
        if not isinstance(expected_concepts, list) or len(expected_concepts) != len(
            required_checks
        ):
            pytest.fail(f"{case_id} semantic rule mapping is out of date", pytrace=False)
        missing_checks = sorted(code for code, passed in required_checks.items() if not passed)
        if missing_checks:
            pytest.fail(
                f"{case_id} is missing required semantic check(s): {','.join(missing_checks)}",
                pytrace=False,
            )
        if case_id == "video-1-control-room-duty" and not any(
            fact.needs_verification
            and any(token in fact.content for token in ("值班", "持证", "证书", "证件"))
            for fact in facts
        ):
            pytest.fail("video 1 verification fact was not marked for human review", pytrace=False)

        ranges_by_fact = [list(fact.media_ranges.all()) for fact in facts]
        evidence_ranges = [item for ranges in ranges_by_fact for item in ranges]
        if any(not ranges for ranges in ranges_by_fact) or any(
            item.material_id != material.id
            or item.start_ms < 0
            or item.end_ms <= item.start_ms
            or item.end_ms > material.duration_ms + 3_000
            for item in evidence_ranges
        ):
            pytest.fail(
                f"{case_id} fact evidence is not traceable to its source timeline", pytrace=False
            )

        transcript_texts = (transcript.full_text,) + tuple(segment.text for segment in segments)
        sensitive_values.extend(transcript_texts)
        sensitive_values.extend(visual_texts)
        sensitive_values.extend(fact_texts)
        case_summaries.append(
            _safe_case_summary(
                case_id=case_id,
                input_sha256=material.sha256,
                transcript_texts=transcript_texts,
                visual_texts=visual_texts,
                fact_texts=fact_texts,
                segment_count=len(segments),
                keyframe_hashes=tuple(item.sha256 for item in keyframes),
                evidence_range_count=len(evidence_ranges),
                required_checks=required_checks,
                forbidden_violations=forbidden_violations,
            )
        )

    fire_law_sha256 = next(
        asset.sha256
        for asset in AUTHORITATIVE_ASSETS
        if asset.relative_path == "testdata/knowledge/reg_ec997c582c9c29a6.pdf"
    )
    command_output = StringIO()
    call_command("initialize_fire_law", stdout=command_output, verbosity=0)
    knowledge_document = KnowledgeDocumentRecord.objects.get(sha256=fire_law_sha256)
    parse_job = ProcessingJob.objects.get(
        task_type=ProcessingJob.TaskType.KNOWLEDGE_PARSE,
        scope_type="knowledge_document",
        scope_id=knowledge_document.id,
    )
    claimed_parse = acquire_job_by_id(
        job_id=parse_job.id,
        worker_id="real-ai:fire-law:parse",
    )
    if claimed_parse is None:
        pytest.fail("knowledge parse job could not be leased", pytrace=False)
    WorkerCommand._execute(claimed_parse.id, claimed_parse.lease_token)
    parse_job.refresh_from_db()
    if parse_job.status != ProcessingJob.Status.SUCCEEDED:
        pytest.fail(
            f"knowledge parse job ended as {parse_job.status} "
            f"({parse_job.safe_error_code or 'no_error_code'})",
            pytrace=False,
        )
    knowledge_document.refresh_from_db()
    embedding_job = ProcessingJob.objects.get(
        task_type=ProcessingJob.TaskType.EMBEDDING,
        scope_type="knowledge_document",
        scope_id=knowledge_document.id,
        expected_scope_revision=knowledge_document.revision,
    )
    claimed_embedding = acquire_job_by_id(
        job_id=embedding_job.id,
        worker_id="real-ai:fire-law:embedding",
    )
    if claimed_embedding is None:
        pytest.fail("embedding job could not be leased", pytrace=False)
    WorkerCommand._execute(claimed_embedding.id, claimed_embedding.lease_token)
    embedding_job.refresh_from_db()
    if embedding_job.status != ProcessingJob.Status.SUCCEEDED:
        pytest.fail(
            f"embedding job ended as {embedding_job.status} "
            f"({embedding_job.safe_error_code or 'no_error_code'})",
            pytrace=False,
        )
    capability_jobs["embedding"].append(embedding_job)

    embeddings = list(
        KnowledgeEmbeddingRecord.objects.filter(
            clause__document=knowledge_document,
            input_revision=knowledge_document.revision,
            status=KnowledgeEmbeddingRecord.Status.SUCCEEDED,
        ).order_by("clause__sequence", "clause_id")
    )
    if len(embeddings) != knowledge_document.clauses.count() or not embeddings:
        pytest.fail("embedding job did not atomically publish every clause vector", pytrace=False)
    if (
        len({item.dimensions for item in embeddings}) != 1
        or len({item.model_fingerprint for item in embeddings}) != 1
    ):
        pytest.fail("embedding publication mixed incompatible vector outputs", pytrace=False)
    embedding_output_sha256 = sha256(
        json.dumps(
            [
                {
                    "dimensions": item.dimensions,
                    "model_fingerprint": item.model_fingerprint,
                    "values": item.values,
                }
                for item in embeddings
            ],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    report = {
        "schema_version": 2,
        "executed_at": datetime.now(UTC).isoformat(),
        "test_version": TEST_VERSION,
        "capabilities": {
            capability: _capability_summary(jobs) for capability, jobs in capability_jobs.items()
        },
        "inputs": case_summaries,
        "embedding": {
            "input_sha256": fire_law_sha256,
            "clause_count": knowledge_document.clauses.count(),
            "vector_count": len(embeddings),
            "dimension_count": len({item.dimensions for item in embeddings}),
            "output_sha256": embedding_output_sha256,
        },
        "redacted_conclusion": {
            "pipeline_job_count": sum(len(jobs) for jobs in capability_jobs.values()),
            "required_check_count": sum(
                int(summary["required_check_count"]) for summary in case_summaries
            ),
            "required_pass_count": sum(
                int(summary["required_pass_count"]) for summary in case_summaries
            ),
            "forbidden_violation_count": sum(
                int(summary["forbidden_violation_count"]) for summary in case_summaries
            ),
        },
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    sensitive_values.extend(
        value
        for capability in ("ASR", "VISION", "LLM", "EMBEDDING")
        for value in (
            os.environ.get(f"FIRE_INSPECT_{capability}_API_KEY", ""),
            os.environ.get(f"FIRE_INSPECT_{capability}_ENDPOINT", ""),
        )
    )
    _assert_no_report_payload_leak(serialized, sensitive_values)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(serialized, encoding="utf-8")
