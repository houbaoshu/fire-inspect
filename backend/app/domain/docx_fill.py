"""Word 模板填充引擎：6 类填充操作（TEMPLATE-ANALYSIS §2/§4.4）。

- 下划线样式：值 run 设 <w:u w:val="single"/>，字体字号继承所在段落（rPr 深拷贝），标签 run 不动；
- 记录表：普通填充不带下划线，检查类型 □→☑（字符级精确替换）；
- 笔录/照片文书：所有填入值带下划线；问答正文整体生成并带下划线；
- 页脚硬编码总页数 → NUMPAGES 域；照片模板按帧整块纵向复制。
"""
import copy, re
from pathlib import Path
import docx
from docx.oxml.ns import qn
from docx.shared import Cm

CHECKBOX, CHECKED = "□", "☑"


# ---------- run 级工具 ----------

def _ref_rpr(paragraph):
    for r in paragraph.runs:
        rpr = r._r.find(qn("w:rPr"))
        if rpr is not None:
            return copy.deepcopy(rpr)
    return None


def _build_run(rpr, text: str, underline: bool):
    from lxml import etree
    r = etree.Element(qn("w:r"))
    if rpr is not None:
        r.append(copy.deepcopy(rpr))
    if underline:
        rpr_el = r.find(qn("w:rPr"))
        if rpr_el is None:
            rpr_el = etree.Element(qn("w:rPr"))
            r.insert(0, rpr_el)
        for u in rpr_el.findall(qn("w:u")):
            rpr_el.remove(u)
        u = etree.SubElement(rpr_el, qn("w:u"))
        u.set(qn("w:val"), "single")
    t = etree.SubElement(r, qn("w:t"))
    t.set(qn("xml:space"), "preserve")
    t.text = text
    return r


def _make_numpages_field():
    from lxml import etree
    fld = etree.Element(qn("w:fldSimple"))
    fld.set(qn("w:instr"), " NUMPAGES ")
    r = etree.SubElement(fld, qn("w:r"))
    t = etree.SubElement(r, qn("w:t"))
    t.text = "1"
    return fld


def _rebuild(paragraph, segments: list[tuple[str, bool]]):
    """重写段落 run 序列。segments: (text, underline)；text=='\x00NUMPAGES' 插入页数域。"""
    rpr = _ref_rpr(paragraph)
    p = paragraph._p
    for r in list(p.findall(qn("w:r"))):
        p.remove(r)
    for text, underline in segments:
        if text == "\x00NUMPAGES":
            p.append(_make_numpages_field())
        elif text:
            p.append(_build_run(rpr, text, underline))


# ---------- 6 类填充操作 ----------

def fill_cell_write(doc, anchor: dict, value: str, underline: bool):
    cell = doc.tables[anchor["table"]].rows[anchor["row"]].cells[anchor["col"]]
    p = cell.paragraphs[0]
    for extra in cell.paragraphs[1:]:  # 空单元格通常一段；多余段落清空
        _rebuild(extra, [])
    _rebuild(p, [(value, underline)])


def _checkbox_rebuild(paragraph, local_target: int):
    """段落内第 local_target 个 □ → ☑，其余保持；逐字符重建保留缩进。"""
    text = paragraph.text
    segments, box_idx, buf = [], 0, ""
    for ch in text:
        if ch == CHECKBOX:
            if buf:
                segments.append((buf, False))
                buf = ""
            segments.append(((CHECKED if box_idx == local_target else CHECKBOX), False))
            box_idx += 1
        else:
            buf += ch
    if buf:
        segments.append((buf, False))
    _rebuild(paragraph, segments)


def fill_checkbox(doc, anchor: dict, selected: str):
    """选中项打勾（字符级）；支持正文段落与表格单元格（跨多段落聚合）。"""
    choices = anchor.get("choices") or []
    if selected not in choices:
        raise ValueError(f"复选值「{selected}」不在选项 {choices} 中")
    target = choices.index(selected)
    if anchor.get("kind") == "cell_checkbox":
        cell = doc.tables[anchor["table"]].rows[anchor["row"]].cells[anchor["col"]]
        idx = 0
        for para in cell.paragraphs:
            n = para.text.count(CHECKBOX)
            if idx <= target < idx + n:
                _checkbox_rebuild(para, target - idx)
                return
            idx += n
        raise ValueError("单元格内未找到复选框")
    _checkbox_rebuild(_locate_paragraph(doc, anchor), target)


def fill_append_after_label(doc, anchor: dict, value: str, underline: bool):
    """标签行追加：值跟在标签后（标签后零空白亦适用）。"""
    paragraph = _locate_paragraph(doc, anchor)
    label = anchor["label"]
    text = paragraph.text
    idx = text.find(label)
    if idx < 0:
        raise ValueError(f"标签定位失败：{label}")
    prefix = text[:idx + len(label)]
    _rebuild(paragraph, [(prefix, False), (" " + value if value else "", underline)])


def fill_replace_spaces(doc, anchor: dict, value: str, underline: bool):
    """行内多字段：替换本标签与下一标签之间的空格段为带下划线的值。"""
    paragraph = _locate_paragraph(doc, anchor)
    text = paragraph.text
    label, next_label = anchor["label"], anchor.get("next_label")
    start = text.find(label)
    if start < 0:
        raise ValueError(f"标签定位失败：{label}")
    val_start = start + len(label)
    val_end = text.find(next_label, val_start) if next_label else len(text)
    if val_end < 0:
        val_end = len(text)
    _rebuild(paragraph, [(text[:val_start], False), (" " + value + " " if value else text[val_start:val_end], underline),
                         (text[val_end:], False)])


def fill_skeleton(doc, anchor: dict, value: str, underline: bool):
    """骨架空白：| 分隔的值按空位顺序填入，固定字（年/月/日/第/号/[]/至）保持。"""
    paragraph = _locate_paragraph(doc, anchor)
    text = paragraph.text
    m = re.search(anchor["pattern"], text)
    if not m:
        raise ValueError(f"骨架定位失败：{anchor['pattern']}")
    region = m.group(0)
    parts = [v.strip() for v in value.split("|")] if value else []
    tokens = re.split(r"([\s　]+)", region)  # 固定字与空白交替
    segments: list[tuple[str, bool]] = [(text[:m.start()], False)]
    vi = 0
    for tok in tokens:
        if not tok:
            continue
        if re.fullmatch(r"[\s　]+", tok):
            if vi < len(parts) and parts[vi]:
                segments.append((parts[vi], underline))
            else:
                segments.append((tok, False))
            vi += 1
        else:
            segments.append((tok, False))
    while vi < len(parts):  # 值多于空位：追加在末尾（如"至"后的结束时间）
        segments.append((" " + parts[vi], underline))
        vi += 1
    segments.append((text[m.end():], False))
    _rebuild(paragraph, segments)


def fill_placeholder_image(doc, anchor: dict, image_path: str, width_cm: float = 12.0):
    """方括号占位段落 → inline 图片（run.add_picture 自动管理 media 部件与关系 ID）。"""
    paragraph = _locate_paragraph(doc, anchor)
    _rebuild(paragraph, [])
    run = paragraph.add_run()
    run.add_picture(image_path, width=Cm(width_cm))


def fill_generate_body(doc, anchor: dict, lines: list[str], underline: bool = True):
    """在锚点段落后生成全新段落（问答正文），继承锚点段落格式，文本带下划线。"""
    anchor_p = _locate_paragraph(doc, anchor)
    ref_p = anchor_p._p
    insert_at = ref_p
    for line in lines:
        new_p = copy.deepcopy(ref_p)          # 继承 pPr（字体/行距）
        for r in list(new_p.findall(qn("w:r"))):
            new_p.remove(r)
        rpr = _ref_rpr(anchor_p)
        new_p.append(_build_run(rpr, line, underline))
        insert_at.addnext(new_p)
        insert_at = new_p


def ensure_update_fields(doc):
    """让 Word 打开时刷新域缓存值（PAGE/NUMPAGES 等），保证总页数正确。"""
    settings_el = doc.settings.element
    if settings_el.find(qn("w:updateFields")) is None:
        from lxml import etree
        el = etree.SubElement(settings_el, qn("w:updateFields"))
        el.set(qn("w:val"), "true")


def fix_footer_numpages(doc):
    """页脚总页数双保险：
    1. 真·硬编码字面数字（不在域内，python-docx 的 p.text 可见）→ NUMPAGES 域；
    2. 域缓存值陈旧（python-docx 看不到，如本模板）→ settings 置 updateFields 强制刷新。
    """
    ensure_update_fields(doc)
    for sec in doc.sections:
        for footer in (sec.footer, sec.first_page_footer, sec.even_page_footer):
            if footer is None:
                continue
            for p in footer.paragraphs:
                if re.search(r"共\s*\d+\s*页", p.text):
                    m = re.search(r"共(\s*)\d+(\s*)页", p.text)
                    _rebuild(p, [(p.text[:m.start()] + "共" + m.group(1), False),
                                 ("\x00NUMPAGES", False),
                                 (m.group(2) + "页" + p.text[m.end():], False)])


# ---------- 定位 ----------

def _locate_paragraph(doc, anchor: dict):
    kind = anchor.get("kind")
    if kind in ("paragraph", "body_anchor"):
        idx = anchor.get("p_index", anchor.get("after_p_index"))
        return doc.paragraphs[idx]
    raise ValueError(f"未知锚点类型：{kind}")


# ---------- 照片文书：按帧整块纵向复制（PRD §16） ----------

def duplicate_photo_block(doc, block_start: int, block_len: int, n_extra: int) -> int:
    """把 [block_start, block_start+block_len) 段落块复制 n_extra 次追加到文末。返回块长。"""
    body = doc.element.body
    paras = doc.paragraphs
    block_elements = [copy.deepcopy(paras[block_start + i]._p) for i in range(block_len)]
    anchor_el = paras[block_start + block_len - 1]._p
    for _ in range(n_extra):
        for el in block_elements:
            new_el = copy.deepcopy(el)
            anchor_el.addnext(new_el)
            anchor_el = new_el
    return block_len


def fill_template(template_path: str, out_path: str, ops: list[dict],
                  photo_block: dict | None = None, fix_footer: bool = True):
    """统一填充入口。ops: [{"fill_op", "anchor", "value", "underline", ...}]。
    photo_block: {"start": int, "len": int, "n_frames": int} 时先整块复制。"""
    doc = docx.Document(template_path)
    if photo_block and photo_block.get("n_frames", 1) > 1:
        duplicate_photo_block(doc, photo_block["start"], photo_block["len"], photo_block["n_frames"] - 1)
    for op in ops:
        fn = op["fill_op"]
        if fn == "cell_write":
            fill_cell_write(doc, op["anchor"], op["value"], op.get("underline", False))
        elif fn == "checkbox":
            fill_checkbox(doc, {**op["anchor"], "choices": op.get("choices", [])}, op["value"])
        elif fn == "append_after_label":
            fill_append_after_label(doc, op["anchor"], op["value"], op.get("underline", True))
        elif fn == "replace_spaces":
            fill_replace_spaces(doc, op["anchor"], op["value"], op.get("underline", True))
        elif fn == "skeleton":
            fill_skeleton(doc, op["anchor"], op["value"], op.get("underline", True))
        elif fn == "replace_placeholder_image":
            fill_placeholder_image(doc, op["anchor"], op["value"])
        elif fn == "generate_body":
            lines = [ln for ln in op["value"].splitlines() if ln.strip()]
            fill_generate_body(doc, op["anchor"], lines, underline=op.get("underline", True))
        else:
            raise ValueError(f"未知填充操作：{fn}")
    if fix_footer:
        fix_footer_numpages(doc)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
