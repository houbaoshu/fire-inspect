"""Validated inputs for deterministic DOCX generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import StrEnum


class InspectionType(StrEnum):
    MASS_EVENT = "大型群众性活动举办前的检查"
    CONSTRUCTION_SITE = "建设工程施工现场检查"
    COMPLAINT = "对举报投诉的核查"
    RECHECK = "复查"
    RESUME_OPERATION = "申请恢复施工、使用、生产、经营的检查"
    LIFT_SEAL = "申请解除临时查封的检查"
    OTHER = "其它检查"


@dataclass(frozen=True, slots=True)
class RecordDocumentData:
    document_number: str
    inspection_date: date
    inspection_type: InspectionType
    unit_name: str
    responsible_person: str
    address: str
    contact_name: str
    contact_phone: str
    inspection_details: str
    remarks: str = ""

    def __post_init__(self) -> None:
        required = {
            "编号": self.document_number,
            "单位名称": self.unit_name,
            "地址": self.address,
            "检查内容": self.inspection_details,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"记录表必填字段为空：{'、'.join(missing)}")

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PhotoSharedFields:
    shooting_location: str
    photographer: str
    shooting_time: datetime | str
    confirmer: str = ""

    def __post_init__(self) -> None:
        if not self.shooting_location.strip() or not self.photographer.strip():
            raise ValueError("照片拍摄地点和摄制人不能为空")
        if isinstance(self.shooting_time, str) and not self.shooting_time.strip():
            raise ValueError("照片拍摄时间不能为空")


@dataclass(frozen=True, slots=True)
class PhotoItem:
    image: bytes
    reflected_content: str
    description: str = ""
    source_name: str = "photo"

    def __post_init__(self) -> None:
        if not self.image:
            raise ValueError("照片内容不能为空")
        if not self.reflected_content.strip():
            raise ValueError("照片反映内容不能为空")


@dataclass(frozen=True, slots=True)
class PhotoDocumentData:
    items: tuple[PhotoItem, ...]
    shared: PhotoSharedFields

    def __post_init__(self) -> None:
        if not self.items:
            raise ValueError("照片文书至少需要一张照片")
        if len(self.items) > 50:
            raise ValueError("照片文书照片数量超出限制")


class QaRole(StrEnum):
    QUESTION = "question"
    ANSWER = "answer"
    UNKNOWN = "unknown"

    @property
    def prefix(self) -> str:
        if self is QaRole.QUESTION:
            return "问："
        if self is QaRole.ANSWER:
            return "答："
        raise ValueError("问答角色尚未人工确认")


@dataclass(frozen=True, slots=True)
class QaItem:
    role: QaRole
    text: str

    def __post_init__(self) -> None:
        if self.role is QaRole.UNKNOWN:
            raise ValueError("问答角色必须人工确认")
        if not self.text.strip():
            raise ValueError("问答正文不能为空")


@dataclass(frozen=True, slots=True)
class InterviewDocumentData:
    interview_number: int
    started_at: datetime
    ended_at: datetime
    location: str
    interviewer_name: str
    interviewer_unit: str
    recorder_name: str
    recorder_unit: str
    interviewee_name: str
    gender: str
    age: str
    birth_date: str
    id_type: str
    id_number: str
    registered_address: str
    current_address: str
    contact: str
    qa_items: tuple[QaItem, ...]

    def __post_init__(self) -> None:
        if self.interview_number < 1:
            raise ValueError("询问次数必须大于零")
        if self.ended_at < self.started_at:
            raise ValueError("询问结束时间不能早于开始时间")
        required = {
            "地点": self.location,
            "询问人": self.interviewer_name,
            "询问人工作单位": self.interviewer_unit,
            "记录人": self.recorder_name,
            "记录人工作单位": self.recorder_unit,
            "被询问人姓名": self.interviewee_name,
            "证件类型": self.id_type,
            "证件号码": self.id_number,
            "联系方式": self.contact,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise ValueError(f"询问笔录必填字段为空：{'、'.join(missing)}")
        if not self.qa_items:
            raise ValueError("询问笔录至少需要一段人工确认问答")
