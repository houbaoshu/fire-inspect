from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from uuid import UUID


class AIOutputError(ValueError):
    pass


class SemanticRole(StrEnum):
    QUESTION = "question"
    ANSWER = "answer"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    material_id: UUID
    start_ms: int
    end_ms: int
    text: str
    suggested_role: SemanticRole = SemanticRole.UNKNOWN

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise AIOutputError("转写片段时间范围无效")
        if not self.text.strip():
            raise AIOutputError("转写片段不能为空")


@dataclass(frozen=True, slots=True)
class TranscriptOutput:
    full_text: str
    segments: tuple[TranscriptSegment, ...]

    def __post_init__(self) -> None:
        if not self.full_text.strip():
            raise AIOutputError("完整转写为空，不能标记成功")
        if not self.segments:
            raise AIOutputError("缺少带时间范围的转写片段")
        if list(self.segments) != sorted(self.segments, key=lambda item: item.start_ms):
            raise AIOutputError("转写片段未按时间排序")
        material_ids = {segment.material_id for segment in self.segments}
        if len(material_ids) != 1:
            raise AIOutputError("一次转写结果不能混入多个材料")

    @property
    def material_id(self) -> UUID:
        return self.segments[0].material_id

    def validate_timeline(self, duration_ms: int, *, edge_tolerance_ms: int = 3_000) -> None:
        if duration_ms <= 0:
            raise AIOutputError("材料时长无效")
        if self.segments[0].start_ms > edge_tolerance_ms:
            raise AIOutputError("转写片段未覆盖材料开头")
        if self.segments[-1].end_ms < duration_ms - edge_tolerance_ms:
            raise AIOutputError("转写片段未覆盖材料结尾")
        if self.segments[-1].end_ms > duration_ms + edge_tolerance_ms:
            raise AIOutputError("转写片段超过材料时间轴")


@dataclass(frozen=True, slots=True)
class VisualDescription:
    material_id: UUID
    timestamp_ms: int
    text: str

    def __post_init__(self) -> None:
        if self.timestamp_ms < 0 or not self.text.strip():
            raise AIOutputError("视觉描述无效")


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    material_id: UUID
    start_ms: int
    end_ms: int
    evidence_type: str = "media_range"

    def __post_init__(self) -> None:
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise AIOutputError("事实证据时间范围无效")


@dataclass(frozen=True, slots=True)
class FactCandidate:
    content: str
    evidence: tuple[EvidenceReference, ...]
    needs_verification: bool = False
    verification_note: str = ""
    is_final_enforcement_conclusion: bool = False

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise AIOutputError("候选事实不能为空")
        if self.is_final_enforcement_conclusion:
            raise AIOutputError("AI 不得生成最终执法或处罚结论")
        if not self.evidence and not self.needs_verification:
            raise AIOutputError("无证据候选事实必须标记待核实")


@dataclass(frozen=True, slots=True)
class FactExtractionOutput:
    facts: tuple[FactCandidate, ...]

    def validate_references(
        self,
        allowed_materials: dict[UUID, int],
    ) -> None:
        for fact in self.facts:
            for reference in fact.evidence:
                duration_ms = allowed_materials.get(reference.material_id)
                if duration_ms is None:
                    raise AIOutputError("AI 返回了无权访问或不属于当前检查的材料引用")
                if reference.end_ms > duration_ms + 3_000:
                    raise AIOutputError("AI 返回的证据时间超过材料范围")


@dataclass(frozen=True, slots=True)
class DocumentPhotoItemDraft:
    """Provider-neutral candidate text for one already selected photo source."""

    source_id: UUID
    reflected_content: str
    description: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, UUID):
            raise AIOutputError("文书候选照片引用格式无效")
        if not isinstance(self.reflected_content, str) or not self.reflected_content.strip():
            raise AIOutputError("照片反映内容候选不能为空")
        if not isinstance(self.description, str):
            raise AIOutputError("照片说明候选格式无效")
        if len(self.reflected_content) > 20_000 or len(self.description) > 20_000:
            raise AIOutputError("照片候选内容过长")


@dataclass(frozen=True, slots=True)
class DocumentDraftOutput:
    """Schema-validated candidate fields returned by a replaceable LLM adapter."""

    document_type: str
    fields: dict[str, object]
    referenced_fact_ids: tuple[UUID, ...] = ()
    photo_items: tuple[DocumentPhotoItemDraft, ...] = ()

    def __post_init__(self) -> None:
        if self.document_type not in {"record", "photo", "interview"}:
            raise AIOutputError("文书候选类型无效")
        if not isinstance(self.fields, dict):
            raise AIOutputError("文书候选字段格式无效")
        for key, value in self.fields.items():
            if not isinstance(key, str) or not key.strip():
                raise AIOutputError("文书候选字段键无效")
            if isinstance(value, (dict, list, tuple, set)):
                raise AIOutputError("文书候选字段值格式无效")
            if isinstance(value, str) and len(value) > 20_000:
                raise AIOutputError("文书候选字段值过长")
        if not self.fields and not self.photo_items:
            raise AIOutputError("文书生成返回空结果，不能标记为成功")
        if len(set(self.referenced_fact_ids)) != len(self.referenced_fact_ids):
            raise AIOutputError("文书候选包含重复事实引用")
        if any(not isinstance(value, UUID) for value in self.referenced_fact_ids):
            raise AIOutputError("文书候选事实引用格式无效")
        photo_source_ids = [item.source_id for item in self.photo_items]
        if len(set(photo_source_ids)) != len(photo_source_ids):
            raise AIOutputError("文书候选包含重复照片引用")


@dataclass(frozen=True, slots=True)
class EmbeddingOutput:
    values: tuple[float, ...]
    model_fingerprint: str

    def __post_init__(self) -> None:
        if not self.values or not isinstance(self.model_fingerprint, str):
            raise AIOutputError("Embedding 结果为空")
        if not self.model_fingerprint.strip():
            raise AIOutputError("Embedding 结果为空")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            for value in self.values
        ):
            raise AIOutputError("Embedding 包含非法数值")
        if not any(float(value) != 0 for value in self.values):
            raise AIOutputError("Embedding 不能是零向量")


class NavigationKind(StrEnum):
    MATERIAL_TIME = "material_time"
    PHOTO = "photo"
    FACT = "fact"
    CLAUSE = "clause"
    DOCUMENT = "document"


@dataclass(frozen=True, slots=True)
class NavigationAction:
    kind: NavigationKind
    target_id: UUID
    material_id: UUID | None = None
    start_ms: int | None = None

    def __post_init__(self) -> None:
        if self.kind is NavigationKind.MATERIAL_TIME:
            if self.material_id is None or self.start_ms is None or self.start_ms < 0:
                raise AIOutputError("视频导航必须包含材料和时间")
        elif self.material_id is not None or self.start_ms is not None:
            raise AIOutputError("非视频导航不能携带视频时间")


@dataclass(frozen=True, slots=True)
class AssistantCitation:
    source_type: str
    source_id: UUID
    excerpt: str

    def __post_init__(self) -> None:
        if not self.excerpt.strip():
            raise AIOutputError("助手引用不能为空")


@dataclass(frozen=True, slots=True)
class AssistantAnswer:
    text: str
    citations: tuple[AssistantCitation, ...] = ()
    navigation: tuple[NavigationAction, ...] = ()

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise AIOutputError("助手回答为空")


def as_tuple(items: Iterable[object]) -> tuple[object, ...]:
    return tuple(items)
