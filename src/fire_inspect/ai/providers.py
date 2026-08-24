from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from math import sqrt
from pathlib import Path
from uuid import UUID

from fire_inspect.acceptance import file_sha256

from .schemas import (
    AssistantAnswer,
    DocumentDraftOutput,
    DocumentPhotoItemDraft,
    EmbeddingOutput,
    EvidenceReference,
    FactCandidate,
    FactExtractionOutput,
    SemanticRole,
    TranscriptOutput,
    TranscriptSegment,
    VisualDescription,
)


class CapabilityUnavailable(RuntimeError):
    pass


class ProviderFailure(RuntimeError):
    pass


class DisabledProvider:
    """Manual-mode adapter: never fabricates a successful AI result."""

    def _unavailable(self) -> None:
        raise CapabilityUnavailable("该 AI 能力未配置，请人工处理或配置后重试")

    def transcribe(self, material_id: UUID, media_path: Path, duration_ms: int) -> TranscriptOutput:
        self._unavailable()

    def describe(self, material_id: UUID, frames: Sequence[tuple[int, Path]]):
        self._unavailable()

    def extract_facts(self, transcript: TranscriptOutput, visual_descriptions):
        self._unavailable()

    def embed(self, texts: Sequence[str]):
        self._unavailable()

    def answer(self, question: str, context: dict[str, object]):
        self._unavailable()

    def draft_document(self, document_type: str, context: dict[str, object]):
        del document_type, context
        self._unavailable()


@dataclass(slots=True)
class FixedResponseProvider:
    """Deterministic offline provider used only in tests and explicit demo mode."""

    # The acceptance fixture selects deterministic semantics from the immutable
    # source-video hash. Real ASR adapters keep receiving the extracted audio.
    transcription_input = "original"

    fixture_name: str = "acceptance-v1"
    fail_capabilities: frozenset[str] = frozenset()

    def _fail_if_requested(self, capability: str) -> None:
        if capability in self.fail_capabilities:
            raise ProviderFailure(f"{capability} 固定故障已触发，可恢复后单独重试")

    def transcribe(self, material_id: UUID, media_path: Path, duration_ms: int) -> TranscriptOutput:
        self._fail_if_requested("asr")
        if duration_ms <= 0:
            raise ProviderFailure("材料时长无效")
        digest = file_sha256(media_path) if media_path.is_file() else ""
        if digest.startswith("2f294e5069511646"):
            texts = (
                "问：消防控制室今天谁值班？",
                "答：值班人员今天休息，目前不在岗，持证和值班安排需要再核实。",
            )
        elif digest.startswith("9e0a71029a5a1ed0"):
            texts = (
                "问：大厅里为什么堆放这些物品？",
                "答：这个区域现在当作仓库使用，用途发生了改变。",
            )
        else:
            texts = ("问：请说明现场情况。", "答：该内容需要检查人员人工核实。")
        midpoint = max(1, duration_ms // 2)
        segments = (
            TranscriptSegment(material_id, 0, midpoint, texts[0], SemanticRole.QUESTION),
            TranscriptSegment(material_id, midpoint, duration_ms, texts[1], SemanticRole.ANSWER),
        )
        output = TranscriptOutput(full_text="\n".join(texts), segments=segments)
        output.validate_timeline(duration_ms)
        return output

    def describe(
        self, material_id: UUID, frames: Sequence[tuple[int, Path]]
    ) -> tuple[VisualDescription, ...]:
        self._fail_if_requested("vision")
        return tuple(
            VisualDescription(material_id, timestamp, "现场候选画面，具体业务含义需要人工确认。")
            for timestamp, _ in frames
        )

    def extract_facts(
        self,
        transcript: TranscriptOutput,
        visual_descriptions: Sequence[VisualDescription],
    ) -> FactExtractionOutput:
        self._fail_if_requested("llm")
        text = transcript.full_text
        material_id = transcript.material_id
        end_ms = transcript.segments[-1].end_ms
        facts: list[FactCandidate] = []
        if "控制室" in text and ("不在岗" in text or "休息" in text):
            facts.extend(
                [
                    FactCandidate(
                        "消防控制室值班人员不在岗或当天休息。",
                        (EvidenceReference(material_id, 0, end_ms),),
                    ),
                    FactCandidate(
                        "消防控制室值班安排及人员持证情况需要核实。",
                        (EvidenceReference(material_id, 0, end_ms),),
                        needs_verification=True,
                        verification_note="转写未提供可核验的证件信息。",
                    ),
                ]
            )
        if "堆放" in text or "仓库" in text or "用途" in text:
            facts.extend(
                [
                    FactCandidate(
                        "大厅或现场存在堆放物品。",
                        (EvidenceReference(material_id, 0, end_ms),),
                    ),
                    FactCandidate(
                        "相关区域被当作仓库使用或用途发生改变。",
                        (EvidenceReference(material_id, 0, end_ms),),
                    ),
                ]
            )
        if not facts:
            facts.append(
                FactCandidate(
                    "现场情况需要检查人员人工核实。",
                    (),
                    needs_verification=True,
                    verification_note="固定响应没有匹配业务语义。",
                )
            )
        return FactExtractionOutput(tuple(facts))

    def embed(self, texts: Sequence[str]) -> tuple[EmbeddingOutput, ...]:
        self._fail_if_requested("embedding")
        results: list[EmbeddingOutput] = []
        for text in texts:
            if not text.strip():
                raise ProviderFailure("不能为缺失文本生成向量")
            digest = sha256(text.encode()).digest()
            raw = [float(byte - 127.5) for byte in digest[:16]]
            norm = sqrt(sum(value * value for value in raw)) or 1.0
            results.append(
                EmbeddingOutput(
                    tuple(value / norm for value in raw),
                    f"fixed:{self.fixture_name}:16",
                )
            )
        return tuple(results)

    def answer(self, question: str, context: dict[str, object]) -> AssistantAnswer:
        self._fail_if_requested("llm")
        if not question.strip():
            raise ProviderFailure("问题不能为空")
        return AssistantAnswer("这是固定响应模式的只读建议；请依据已确认事实和来源人工判断。")

    def draft_document(self, document_type: str, context: dict[str, object]) -> DocumentDraftOutput:
        """Build a deterministic draft solely from the supplied reviewed sources."""

        self._fail_if_requested("llm")
        inspection = context.get("inspection")
        facts = context.get("facts")
        photo_items = context.get("photo_items")
        confirmed_qa = context.get("confirmed_qa_items")
        if not isinstance(inspection, dict):
            raise ProviderFailure("文书初稿缺少检查共享字段，未保存本次结果")
        if not isinstance(facts, list) or not isinstance(photo_items, list):
            raise ProviderFailure("文书初稿输入格式无效，未保存本次结果")
        if not isinstance(confirmed_qa, list):
            raise ProviderFailure("文书初稿问答输入格式无效，未保存本次结果")

        try:
            referenced_fact_ids = tuple(UUID(str(item["id"])) for item in facts)
            fact_content = {UUID(str(item["id"])): str(item["content"]).strip() for item in facts}
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderFailure("文书初稿事实输入格式无效，未保存本次结果") from exc

        if document_type == "record":
            details = "\n".join(value for value in fact_content.values() if value)
            if not details:
                details = str(inspection.get("general_notes") or "").strip()
            return DocumentDraftOutput(
                document_type="record",
                fields={"inspection_content": details},
                referenced_fact_ids=referenced_fact_ids if fact_content else (),
            )

        if document_type == "photo":
            drafts: list[DocumentPhotoItemDraft] = []
            used_fact_ids: list[UUID] = []
            for item in photo_items:
                try:
                    source_id = UUID(str(item["source_id"]))
                    related = tuple(UUID(str(value)) for value in item.get("related_fact_ids", []))
                except (KeyError, TypeError, ValueError) as exc:
                    raise ProviderFailure("文书初稿照片引用格式无效，未保存本次结果") from exc
                source_facts = [fact_content[value] for value in related if value in fact_content]
                if not source_facts:
                    source_facts = list(fact_content.values())
                    related = tuple(fact_content)
                for fact_id in related:
                    if fact_id in fact_content and fact_id not in used_fact_ids:
                        used_fact_ids.append(fact_id)
                reflected = "；".join(value.rstrip("。；") for value in source_facts if value)
                if reflected:
                    reflected += "。"
                else:
                    reflected = "所选照片内容待检查人员人工核对。"
                source_label = str(item.get("source_label") or "所选照片").strip()
                drafts.append(
                    DocumentPhotoItemDraft(
                        source_id=source_id,
                        reflected_content=reflected,
                        description=f"来源：{source_label}；具体内容以人工核对为准。",
                    )
                )
            personnel = inspection.get("personnel")
            photographer = ""
            if isinstance(personnel, list) and personnel and isinstance(personnel[0], dict):
                photographer = str(personnel[0].get("name") or "").strip()
            return DocumentDraftOutput(
                document_type="photo",
                fields={
                    "shooting_location": str(inspection.get("unit_address") or "").strip(),
                    "photographer": photographer,
                    "shooting_time": str(inspection.get("inspection_at") or "").strip(),
                    "confirmer": "",
                },
                referenced_fact_ids=tuple(used_fact_ids),
                photo_items=tuple(drafts),
            )

        if document_type == "interview":
            personnel = inspection.get("personnel")
            people = (
                [item for item in personnel if isinstance(item, dict)]
                if isinstance(personnel, list)
                else []
            )
            interviewer = people[0] if people else {}
            recorder = people[1] if len(people) > 1 else interviewer
            return DocumentDraftOutput(
                document_type="interview",
                fields={
                    "interview_number": 1,
                    "started_at": str(inspection.get("inspection_at") or "").strip(),
                    "ended_at": str(inspection.get("inspection_at") or "").strip(),
                    "location": str(inspection.get("unit_address") or "").strip(),
                    "interviewer_name": str(interviewer.get("name") or "").strip(),
                    "interviewer_unit": str(interviewer.get("work_unit") or "").strip(),
                    "recorder_name": str(recorder.get("name") or "").strip(),
                    "recorder_unit": str(recorder.get("work_unit") or "").strip(),
                },
            )
        raise ProviderFailure("文书类型不受支持，未保存本次结果")
