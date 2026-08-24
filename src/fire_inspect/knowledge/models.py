from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from uuid import UUID, uuid4


class KnowledgeCategory(StrEnum):
    LAW = "law"
    STANDARD = "standard"
    LOCAL_RULE = "local_rule"
    INTERNAL = "internal"
    OTHER = "other"


class SourceVerification(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class Validity(StrEnum):
    UNKNOWN = "unknown"
    EFFECTIVE = "effective"
    EXPIRED = "expired"


class KnowledgeMetadataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    title: str
    category: KnowledgeCategory
    original_path: Path
    sha256: str
    issuer: str = ""
    source_reference: str = ""
    version: str = ""
    publication_date: date | None = None
    source_verification: SourceVerification = SourceVerification.PENDING
    validity: Validity = Validity.UNKNOWN
    declared_internal: bool = False
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise KnowledgeMetadataError("知识文档标题不能为空")
        if self.declared_internal and self.category is not KnowledgeCategory.INTERNAL:
            raise KnowledgeMetadataError("内部制度只能归类为内部制度")
        if self.source_verification is SourceVerification.VERIFIED and (
            not self.issuer.strip() or not self.source_reference.strip()
        ):
            raise KnowledgeMetadataError("核验来源需要发布机关和正式来源")
        if self.validity is not Validity.UNKNOWN and (
            self.source_verification is not SourceVerification.VERIFIED
        ):
            raise KnowledgeMetadataError("来源未核验时不能声明现行或失效")

    @property
    def source_label(self) -> str:
        return "已核验" if self.source_verification is SourceVerification.VERIFIED else "待核验"


@dataclass(frozen=True, slots=True)
class KnowledgeClause:
    document_id: UUID
    sequence: int
    clause_identifier: str
    content: str
    page_start: int
    page_end: int
    heading: str = ""
    chapter: str = ""
    id: UUID | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("条款顺序必须从 1 开始")
        if not self.clause_identifier or not self.content:
            raise ValueError("条款编号和正文不能为空")
        if self.page_start < 1 or self.page_end < self.page_start:
            raise ValueError("条款页码范围无效")
