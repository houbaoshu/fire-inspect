"""Deterministic recognition for the three authoritative Word templates."""

from __future__ import annotations

import re
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

from lxml import etree

from .domain import (
    FillStrategy,
    RegionLocator,
    RepeatScope,
    TemplateDocumentType,
    TemplateRecognition,
    TemplateRegion,
)
from .security import DocxSource, read_docx_source, validate_docx

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}

PHOTO_SHA = "6199a905ebd4a0453c90982f5a23987ea54e15ef0c1e8e004b1e50be3d13cdac"
INTERVIEW_SHA = "e15b011e14e0429d6ffc90a22dddf9682d402814b0d6e11b01344cceb53a0819"
RECORD_SHA = "37726202a5ae32b96230504388611c9cc824b3cc1abb798b52efd091f72baae5"


def _fingerprint(anchor: str, path: str) -> str:
    return sha256(f"{path}\0{anchor}".encode()).hexdigest()[:20]


def _region(
    key: str,
    name: str,
    scope: RepeatScope,
    strategy: FillStrategy,
    path: str,
    anchor: str,
    sequence: int,
    *,
    required: bool = True,
    confidence: float = 0.99,
    style_policy: tuple[tuple[str, object], ...] = (),
) -> TemplateRegion:
    return TemplateRegion(
        business_field_key=key,
        display_name=name,
        repeat_scope=scope,
        fill_strategy=strategy,
        locator=RegionLocator(
            part="word/document.xml",
            path=path,
            anchor=anchor,
            context_fingerprint=_fingerprint(anchor, path),
        ),
        confidence=confidence,
        required=required,
        sequence=sequence,
        style_policy=style_policy,
    )


def _photo_regions() -> tuple[TemplateRegion, ...]:
    return (
        _region(
            "photo",
            "照片",
            RepeatScope.PER_ITEM,
            FillStrategy.IMAGE_REGION,
            "body/p[1]",
            "[此处插入照片]",
            1,
            style_policy=(("max_width_inches", 6.0), ("max_height_inches", 5.0)),
        ),
        _region(
            "reflected_content",
            "反映内容",
            RepeatScope.PER_ITEM,
            FillStrategy.LABEL_SUFFIX,
            "body/p[3]",
            "反映内容：",
            2,
        ),
        _region(
            "shooting_location",
            "拍摄地点",
            RepeatScope.DOCUMENT_COMMON,
            FillStrategy.LABEL_SUFFIX,
            "body/p[4]",
            "拍摄地点：",
            3,
        ),
        _region(
            "photographer",
            "摄制人",
            RepeatScope.DOCUMENT_COMMON,
            FillStrategy.LABEL_SUFFIX,
            "body/p[5]",
            "摄制人：",
            4,
        ),
        _region(
            "shooting_time",
            "拍摄时间",
            RepeatScope.DOCUMENT_COMMON,
            FillStrategy.LABEL_SUFFIX,
            "body/p[6]",
            "拍摄时间：",
            5,
        ),
        _region(
            "description",
            "说明",
            RepeatScope.PER_ITEM,
            FillStrategy.LABEL_SUFFIX,
            "body/p[7]",
            "说明：",
            6,
            required=False,
        ),
        _region(
            "confirmer",
            "确认人",
            RepeatScope.DOCUMENT_COMMON,
            FillStrategy.LABEL_SUFFIX,
            "body/p[8]",
            "确认人：",
            7,
            required=False,
        ),
    )


def _record_regions() -> tuple[TemplateRegion, ...]:
    return (
        _region(
            "document_number",
            "编号",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[4]",
            "编号:",
            1,
        ),
        _region(
            "inspection_date",
            "日期",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[5]",
            "检查时间:",
            2,
        ),
        _region(
            "inspection_type",
            "检查类型",
            RepeatScope.SINGLE,
            FillStrategy.CHECKBOX,
            "body/tbl[1]/tr[1]/tc[1]",
            "检查类型：□",
            3,
        ),
        _region(
            "unit_name",
            "单位名称",
            RepeatScope.SINGLE,
            FillStrategy.TABLE_CELL,
            "body/tbl[1]/tr[2]/tc[3]",
            "单位（场所）名称",
            4,
        ),
        _region(
            "responsible_person",
            "负责人",
            RepeatScope.SINGLE,
            FillStrategy.TABLE_CELL,
            "body/tbl[1]/tr[2]/tc[5]",
            "法定代表/主要负责人",
            5,
        ),
        _region(
            "address",
            "地址",
            RepeatScope.SINGLE,
            FillStrategy.TABLE_CELL,
            "body/tbl[1]/tr[3]/tc[3]",
            "地址",
            6,
        ),
        _region(
            "contact",
            "联系人及电话",
            RepeatScope.SINGLE,
            FillStrategy.TABLE_CELL,
            "body/tbl[1]/tr[3]/tc[5]",
            "联系人及联系电话",
            7,
        ),
        _region(
            "inspection_details",
            "检查内容和情况",
            RepeatScope.SINGLE,
            FillStrategy.TABLE_CELL,
            "body/tbl[1]/tr[4]/tc[2]",
            "检查内容和情况",
            8,
        ),
        _region(
            "remarks_and_signatures",
            "备注及签名区域",
            RepeatScope.SINGLE,
            FillStrategy.PRESERVE_BLANK,
            "body/tbl[1]/tr[5]/tc[2];body/p[6:9]",
            "备注|消防监督检查员（签名）|被检查单位随同检查人员（签名）",
            9,
            required=False,
            style_policy=(("remarks", "table_cell"), ("signatures", "preserve_blank")),
        ),
    )


def _interview_regions() -> tuple[TemplateRegion, ...]:
    return (
        _region(
            "interview_number",
            "询问次数",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[1]",
            "第   次",
            1,
        ),
        _region(
            "time_range",
            "起止时间",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[3]",
            "时间：|至",
            2,
        ),
        _region(
            "location",
            "地点",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[4]",
            "地点：",
            3,
        ),
        _region(
            "interviewer",
            "询问人及工作单位",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[5]",
            "询问人（签名）：|工作单位：",
            4,
        ),
        _region(
            "recorder",
            "记录人及工作单位",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[6]",
            "记录人（签名）：|工作单位：",
            5,
        ),
        _region(
            "interviewee",
            "被询问人基础信息",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[7]",
            "被询问人姓名|性别|年龄|出生日期",
            6,
        ),
        _region(
            "identity_document",
            "证件",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[8]",
            "证件类型|证件号码",
            7,
        ),
        _region(
            "addresses",
            "地址",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[9:10]",
            "户籍所在地|现住址",
            8,
        ),
        _region(
            "contact",
            "联系方式",
            RepeatScope.SINGLE,
            FillStrategy.LABEL_SUFFIX,
            "body/p[11]",
            "联系方式：",
            9,
        ),
        _region(
            "qa_body",
            "问答正文",
            RepeatScope.PER_ITEM,
            FillStrategy.BODY,
            "body/p[12]",
            "空白正文锚点",
            10,
        ),
    )


_KNOWN = {
    PHOTO_SHA: (TemplateDocumentType.PHOTO, _photo_regions),
    INTERVIEW_SHA: (TemplateDocumentType.INTERVIEW, _interview_regions),
    RECORD_SHA: (TemplateDocumentType.RECORD, _record_regions),
}


def _paragraph_text(element: etree._Element) -> str:
    return "".join(element.xpath(".//w:t/text()", namespaces=NS))


def _unknown_recognition(*, filename: str, digest: str, payload: bytes) -> TemplateRecognition:
    with ZipFile(BytesIO(payload)) as archive:
        root = etree.fromstring(
            archive.read("word/document.xml"),
            parser=etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False),
        )

    warnings: list[str] = []
    if root.xpath(".//w:sdt", namespaces=NS):
        warnings.append("检测到 Word 内容控件，需管理员人工映射")
    if root.xpath(".//w:txbxContent", namespaces=NS):
        warnings.append("检测到浮动文本框，需管理员人工映射")
    if root.xpath(".//w:tbl//w:tbl", namespaces=NS):
        warnings.append("检测到复杂嵌套表格，需管理员人工映射")

    regions: list[TemplateRegion] = []
    label_pattern = re.compile(r"([\u4e00-\u9fffA-Za-z0-9（）()]{1,24})[：:]")
    for paragraph_index, paragraph in enumerate(
        root.xpath(".//w:body/w:p", namespaces=NS), start=1
    ):
        text = _paragraph_text(paragraph)
        if re.search(r"_{2,}", text):
            warnings.append(f"第 {paragraph_index} 段使用下划线字符空白，需人工确认映射")
        match = label_pattern.search(text)
        if not match:
            continue
        label = match.group(1)
        key = f"manual_{len(regions) + 1:03d}"
        regions.append(
            _region(
                key,
                label,
                RepeatScope.SINGLE,
                FillStrategy.LABEL_SUFFIX,
                f"body/p[{paragraph_index}]",
                f"{label}：",
                len(regions) + 1,
                required=False,
                confidence=0.45,
            )
        )
    if not regions:
        warnings.append("未自动识别出可靠业务区域，请管理员人工补充映射")
    return TemplateRecognition(
        filename=filename,
        sha256=digest,
        document_type=TemplateDocumentType.UNKNOWN,
        regions=tuple(regions),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def recognize_template(
    source: DocxSource,
    *,
    filename: str | None = None,
) -> TemplateRecognition:
    report = validate_docx(source, filename=filename)
    payload = read_docx_source(source)
    known = _KNOWN.get(report.sha256)
    if known is not None:
        document_type, region_factory = known
        return TemplateRecognition(
            filename=report.normalized_filename,
            sha256=report.sha256,
            document_type=document_type,
            regions=region_factory(),
        )
    return _unknown_recognition(
        filename=report.normalized_filename,
        digest=report.sha256,
        payload=payload,
    )
