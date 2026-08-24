from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document

from fire_inspect.templates import (
    FillStrategy,
    MappingState,
    RepeatScope,
    TemplateDocumentType,
    TemplateMappingError,
    add_region,
    confirm_mapping,
    create_mapping_draft,
    recognize_template,
    revise_mapping,
    update_region,
)

EXPECTED_REGIONS = {
    "照片.docx": {
        "照片",
        "反映内容",
        "拍摄地点",
        "摄制人",
        "拍摄时间",
        "说明",
        "确认人",
    },
    "询问笔录.docx": {
        "询问次数",
        "起止时间",
        "地点",
        "询问人及工作单位",
        "记录人及工作单位",
        "被询问人基础信息",
        "证件",
        "地址",
        "联系方式",
        "问答正文",
    },
    "监督检查记录表.docx": {
        "编号",
        "日期",
        "检查类型",
        "单位名称",
        "负责人",
        "地址",
        "联系人及电话",
        "检查内容和情况",
        "备注及签名区域",
    },
}


@pytest.mark.parametrize("filename", EXPECTED_REGIONS)
def test_authoritative_templates_have_all_stable_chinese_business_regions(
    template_dir: Path, filename: str
) -> None:
    before = (template_dir / filename).read_bytes()
    result = recognize_template(template_dir / filename)

    assert {region.display_name for region in result.regions} == EXPECTED_REGIONS[filename]
    assert all(region.confidence >= 0.9 for region in result.regions)
    assert all(region.locator.anchor for region in result.regions)
    assert sha256((template_dir / filename).read_bytes()).digest() == sha256(before).digest()


def test_authoritative_template_types_and_fill_strategies(template_dir: Path) -> None:
    photo = recognize_template(template_dir / "照片.docx")
    interview = recognize_template(template_dir / "询问笔录.docx")
    record = recognize_template(template_dir / "监督检查记录表.docx")

    assert photo.document_type is TemplateDocumentType.PHOTO
    assert interview.document_type is TemplateDocumentType.INTERVIEW
    assert record.document_type is TemplateDocumentType.RECORD

    photo_by_name = {region.display_name: region for region in photo.regions}
    assert photo_by_name["照片"].fill_strategy is FillStrategy.IMAGE_REGION
    assert photo_by_name["反映内容"].repeat_scope is RepeatScope.PER_ITEM
    assert photo_by_name["拍摄地点"].repeat_scope is RepeatScope.DOCUMENT_COMMON

    record_by_name = {region.display_name: region for region in record.regions}
    assert record_by_name["检查类型"].fill_strategy is FillStrategy.CHECKBOX
    assert record_by_name["备注及签名区域"].fill_strategy is FillStrategy.PRESERVE_BLANK

    interview_by_name = {region.display_name: region for region in interview.regions}
    assert interview_by_name["问答正文"].fill_strategy is FillStrategy.BODY
    assert interview_by_name["问答正文"].repeat_scope is RepeatScope.PER_ITEM


def test_public_regions_never_expose_internal_locators_or_hashes(template_dir: Path) -> None:
    result = recognize_template(template_dir / "询问笔录.docx")
    public = result.public_regions()

    assert public
    assert all(set(item) == {"显示名称", "填充方式", "置信度", "启用", "必填"} for item in public)
    assert all("locator" not in repr(item).lower() for item in public)
    assert all(result.sha256 not in repr(item) for item in public)


def test_mapping_is_editable_as_draft_and_confirmed_mapping_is_immutable(
    template_dir: Path,
) -> None:
    draft = create_mapping_draft(recognize_template(template_dir / "照片.docx"))
    renamed = update_region(draft, "description", display_name="现场说明", enabled=False)
    supplemented = add_region(
        renamed,
        business_field_key="manual_note",
        display_name="人工补充区域",
        repeat_scope=RepeatScope.PER_ITEM,
        fill_strategy=FillStrategy.LABEL_SUFFIX,
        anchor="人工标定",
    )

    assert supplemented.state is MappingState.DRAFT
    assert supplemented.region("description").display_name == "现场说明"
    assert not supplemented.region("description").enabled
    assert supplemented.region("manual_note").confidence == 0.0

    confirmed = confirm_mapping(supplemented)
    assert confirmed.state is MappingState.CONFIRMED
    with pytest.raises(TemplateMappingError, match="已确认"):
        update_region(confirmed, "description", enabled=True)

    next_draft = revise_mapping(confirmed)
    assert next_draft.state is MappingState.DRAFT
    assert next_draft.revision_no == confirmed.revision_no + 1


def test_required_region_cannot_be_disabled_when_mapping_is_confirmed(
    template_dir: Path,
) -> None:
    draft = create_mapping_draft(recognize_template(template_dir / "监督检查记录表.docx"))
    invalid = update_region(draft, "inspection_type", enabled=False)
    with pytest.raises(TemplateMappingError, match="必填"):
        confirm_mapping(invalid)


def test_unknown_underscore_shape_is_preserved_and_reported_for_manual_mapping() -> None:
    document = Document()
    document.add_paragraph("自定义字段：________")
    stream = BytesIO()
    document.save(stream)
    original = stream.getvalue()

    result = recognize_template(original, filename="unknown.docx")

    assert result.document_type is TemplateDocumentType.UNKNOWN
    assert any("下划线字符" in warning for warning in result.warnings)
    assert any(region.display_name == "自定义字段" for region in result.regions)
    assert original == stream.getvalue()
