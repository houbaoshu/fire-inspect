"""Framework-independent template recognition and mapping value objects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any


class TemplateDocumentType(StrEnum):
    RECORD = "record"
    PHOTO = "photo"
    INTERVIEW = "interview"
    UNKNOWN = "unknown"


class RepeatScope(StrEnum):
    SINGLE = "single"
    DOCUMENT_COMMON = "document_common"
    PER_ITEM = "per_item"


class FillStrategy(StrEnum):
    LABEL_SUFFIX = "label_suffix"
    TABLE_CELL = "table_cell"
    CHECKBOX = "checkbox"
    IMAGE_REGION = "image_region"
    BODY = "body"
    FOOTER_FIELD = "footer_field"
    PRESERVE_BLANK = "preserve_blank"


class MappingState(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    SUPERSEDED = "superseded"


class TemplateMappingError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RegionLocator:
    part: str
    path: str
    anchor: str
    context_fingerprint: str


@dataclass(frozen=True, slots=True)
class TemplateRegion:
    business_field_key: str
    display_name: str
    repeat_scope: RepeatScope
    fill_strategy: FillStrategy
    locator: RegionLocator
    confidence: float
    enabled: bool = True
    required: bool = True
    sequence: int = 0
    style_policy: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.business_field_key or not self.display_name.strip():
            raise TemplateMappingError("模板区域键和中文业务名称不能为空")
        if not 0 <= self.confidence <= 1:
            raise TemplateMappingError("模板区域置信度必须在 0 到 1 之间")

    def public_view(self) -> dict[str, object]:
        """Return UI-safe fields, intentionally excluding locators and hashes."""

        return {
            "显示名称": self.display_name,
            "填充方式": self.fill_strategy.value,
            "置信度": self.confidence,
            "启用": self.enabled,
            "必填": self.required,
        }


@dataclass(frozen=True, slots=True)
class TemplateRecognition:
    filename: str
    sha256: str
    document_type: TemplateDocumentType
    regions: tuple[TemplateRegion, ...]
    warnings: tuple[str, ...] = ()
    recognizer_version: str = "1.0"

    def public_regions(self) -> tuple[dict[str, object], ...]:
        return tuple(region.public_view() for region in self.regions)


@dataclass(frozen=True, slots=True)
class TemplateMappingRevision:
    template_sha256: str
    document_type: TemplateDocumentType
    revision_no: int
    state: MappingState
    recognizer_version: str
    regions: tuple[TemplateRegion, ...]

    def region(self, business_field_key: str) -> TemplateRegion:
        for region in self.regions:
            if region.business_field_key == business_field_key:
                return region
        raise TemplateMappingError("模板映射中不存在该业务区域")


def create_mapping_draft(recognition: TemplateRecognition) -> TemplateMappingRevision:
    return TemplateMappingRevision(
        template_sha256=recognition.sha256,
        document_type=recognition.document_type,
        revision_no=1,
        state=MappingState.DRAFT,
        recognizer_version=recognition.recognizer_version,
        regions=recognition.regions,
    )


def _require_draft(mapping: TemplateMappingRevision) -> None:
    if mapping.state is not MappingState.DRAFT:
        raise TemplateMappingError("已确认的模板映射不可原位修改，请先创建新草稿")


def update_region(
    mapping: TemplateMappingRevision,
    business_field_key: str,
    *,
    display_name: str | None = None,
    enabled: bool | None = None,
    fill_strategy: FillStrategy | None = None,
    locator: RegionLocator | None = None,
) -> TemplateMappingRevision:
    _require_draft(mapping)
    current = mapping.region(business_field_key)
    updated = replace(
        current,
        display_name=current.display_name if display_name is None else display_name.strip(),
        enabled=current.enabled if enabled is None else enabled,
        fill_strategy=current.fill_strategy if fill_strategy is None else fill_strategy,
        locator=current.locator if locator is None else locator,
    )
    regions = tuple(
        updated if region.business_field_key == business_field_key else region
        for region in mapping.regions
    )
    return replace(mapping, regions=regions)


def add_region(
    mapping: TemplateMappingRevision,
    *,
    business_field_key: str,
    display_name: str,
    repeat_scope: RepeatScope,
    fill_strategy: FillStrategy,
    anchor: str,
    part: str = "word/document.xml",
    path: str = "manual",
    required: bool = False,
) -> TemplateMappingRevision:
    _require_draft(mapping)
    if any(region.business_field_key == business_field_key for region in mapping.regions):
        raise TemplateMappingError("模板业务区域键重复")
    locator = RegionLocator(
        part=part,
        path=path,
        anchor=anchor,
        context_fingerprint="manual",
    )
    region = TemplateRegion(
        business_field_key=business_field_key,
        display_name=display_name.strip(),
        repeat_scope=repeat_scope,
        fill_strategy=fill_strategy,
        locator=locator,
        confidence=0.0,
        required=required,
        sequence=len(mapping.regions) + 1,
    )
    return replace(mapping, regions=(*mapping.regions, region))


def confirm_mapping(mapping: TemplateMappingRevision) -> TemplateMappingRevision:
    _require_draft(mapping)
    missing = [
        region.display_name
        for region in mapping.regions
        if region.required and (not region.enabled or not region.locator.anchor.strip())
    ]
    if missing:
        raise TemplateMappingError(f"必填模板区域未启用或未定位：{'、'.join(missing)}")
    keys = [region.business_field_key for region in mapping.regions]
    if len(keys) != len(set(keys)):
        raise TemplateMappingError("模板业务区域键重复")
    return replace(mapping, state=MappingState.CONFIRMED)


def revise_mapping(mapping: TemplateMappingRevision) -> TemplateMappingRevision:
    if mapping.state is not MappingState.CONFIRMED:
        raise TemplateMappingError("只有已确认映射可以创建下一版草稿")
    return replace(
        mapping,
        revision_no=mapping.revision_no + 1,
        state=MappingState.DRAFT,
    )
