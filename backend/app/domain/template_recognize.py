"""无占位符 Word 模板自动识别：确定性规则引擎（TEMPLATE-ANALYSIS §4.3 六条规则）。

规则优先级：
 1. 表格"标签-值"对（空单元格 + 左/上邻接中文标签）        → cell_write（高置信）
 2. 字面复选框组 □+选项文字                                 → checkbox（高置信）
 3. 标签行（标签：后可无空格）                              → append_after_label（高置信）
 4. 行内多字段切分（标签：␣+标签：…，靠标签匹配非空格阈值） → replace_spaces（中置信）
 5. 骨架空白（年 月 日 / 第 X 号/次 / [ ] / 至）            → skeleton（中置信）
 6. 方括号占位段落 […]                                     → placeholder（中置信）
 7. 完全空白正文区（询问笔录问答区）                         → generate_body（中置信）
锚点采用"块索引 + 定位文本"双保险：填充时按索引定位并校验文本，模板变更时不静默错位。
"""
import re
import docx
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph

CHECKBOX = "□"
LABEL_RE = re.compile(r"([^：:\s]{1,20})[：:]")
CN_LABEL = re.compile(r"^[一-鿿（）()／/·\s]{1,15}$")

# 标签词典（TEMPLATE-ANALYSIS §3 已确认 + 常见扩展），用于行内多字段切分
LABEL_DICT = {
    "单位（场所）名称", "法定代表/主要负责人", "法定代表人", "地址", "联系人及联系电话",
    "检查内容和情况", "备注", "反映内容", "拍摄地点", "摄制人", "摄影人", "拍摄时间", "说明", "确认人",
    "时间", "地点", "询问人（签名）", "记录人（签名）", "工作单位", "被询问人姓名", "性别", "年龄",
    "出生日期", "证件类型", "证件号码", "户籍所在地", "现住址", "联系方式",
    "消防监督检查员（签名）", "被检查单位随同检查人员（签名）", "编号", "检查时间",
}
SHARED_LABELS = {"拍摄地点", "摄制人", "摄影人", "拍摄时间", "确认人"}     # 照片文书同源字段（PRD §16）
PER_FRAME_LABELS = {"反映内容", "说明"}                                   # 逐帧字段


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _norm_label(raw: str) -> str:
    """标签归一：去空白、去尾冒号；标签词典内优先匹配（处理"单位（场所）名称"等）。"""
    t = _clean(raw).rstrip("：:")
    if t in LABEL_DICT:
        return t
    for known in sorted(LABEL_DICT, key=len, reverse=True):
        if t.endswith(known):
            return known
    return t


def _is_label(text: str) -> bool:
    t = _clean(text).rstrip("：:")
    return bool(t) and (t in LABEL_DICT or (len(t) <= 12 and CN_LABEL.match(t) is not None))


def _p(text) -> dict:
    return {"text": text}


def recognize(path: str, doc_type: str) -> tuple[list[dict], list[str]]:
    """返回 (fields, warnings)。field = {field_label, fill_op, anchor, confidence, options, shared, per_frame}"""
    d = docx.Document(path)
    fields: list[dict] = []
    warnings: list[str] = []
    paras: list[Paragraph] = d.paragraphs
    ptexts = [p.text for p in paras]

    # ---- 规则 1：表格标签-值对 ----
    for ti, tbl in enumerate(d.tables):
        _recognize_table(tbl, ti, fields)

    # ---- 段落级规则 ----
    consumed: set[int] = set()
    for i, p in enumerate(paras):
        text = p.text
        stripped = text.strip()
        if not stripped:
            continue
        # 规则 2：复选框组
        if CHECKBOX in text:
            options = [m.group(1).strip() for m in re.finditer(r"□\s*([^□]+?)(?=□|$)", text)]
            fields.append({"field_label": "检查类型", "fill_op": "checkbox",
                           "anchor": {"kind": "paragraph", "p_index": i, "match": "□"},
                           "confidence": "high",
                           "options": {"choices": options}, "shared": False, "per_frame": False})
            consumed.add(i)
            continue
        # 规则 6：整段方括号占位
        if re.fullmatch(r"\[[^\]]*\]", stripped):
            op = "replace_placeholder_image" if "照片" in stripped or "相片" in stripped or "图片" in stripped else "replace_placeholder"
            fields.append({"field_label": "照片图像" if op == "replace_placeholder_image" else stripped.strip("[]"),
                           "fill_op": op, "anchor": {"kind": "paragraph", "p_index": i, "match": stripped},
                           "confidence": "medium", "options": {}, "shared": False,
                           "per_frame": op == "replace_placeholder_image"})
            consumed.add(i)
            continue
        # 规则 5：骨架空白（年 月 日 / 第 号/次 / [ ] / 至）
        skel = _match_skeleton(text, i)
        if skel:
            fields.extend(skel)
            consumed.add(i)
            continue
        # 规则 4：行内多字段（≥2 个标签）
        labels = list(LABEL_RE.finditer(text))
        known = [m for m in labels if _norm_label(m.group(1)) in LABEL_DICT or len(_clean(m.group(1))) <= 12]
        if len(known) >= 2:
            for j, m in enumerate(known):
                label = _norm_label(m.group(1))
                nxt = known[j + 1].group(0) if j + 1 < len(known) else None
                fields.append({"field_label": label, "fill_op": "replace_spaces",
                               "anchor": {"kind": "paragraph", "p_index": i,
                                          "label": m.group(0), "next_label": nxt},
                               "confidence": "medium", "options": {},
                               "shared": label in SHARED_LABELS, "per_frame": label in PER_FRAME_LABELS})
            consumed.add(i)
            continue
        # 规则 3：单标签行（含标签后零空白：地点：/（签名）：）
        if len(known) == 1:
            m = known[0]
            tail = text[m.end():]
            if not tail.strip():  # 冒号后只有空白或什么都没有
                label = _norm_label(m.group(1))
                fields.append({"field_label": label, "fill_op": "append_after_label",
                               "anchor": {"kind": "paragraph", "p_index": i, "label": m.group(0)},
                               "confidence": "high", "options": {},
                               "shared": label in SHARED_LABELS, "per_frame": label in PER_FRAME_LABELS})
                consumed.add(i)

    # ---- 规则 7：问答正文区（询问笔录：表头之后完全空白）----
    if doc_type == "interrogation":
        last_field_p = max((f["anchor"].get("p_index", -1) for f in fields
                            if f["anchor"].get("kind") == "paragraph"), default=-1)
        tail_non_empty = [t for t in ptexts[last_field_p + 1:] if t.strip()] if last_field_p >= 0 else ptexts
        has_qa = any(re.search(r"问[：:].*答[：:]", t) for t in tail_non_empty)
        if last_field_p >= 0 and not has_qa:
            fields.append({"field_label": "问答正文", "fill_op": "generate_body",
                           "anchor": {"kind": "body_anchor", "after_p_index": last_field_p},
                           "confidence": "medium", "options": {}, "shared": False, "per_frame": False})

    # ---- 页脚硬编码总页数检查（TEMPLATE-ANALYSIS §3.2）----
    for sec in d.sections:
        for footer, tag in ((sec.footer, "默认"), (sec.first_page_footer, "首页"), (sec.even_page_footer, "偶数页")):
            if footer is None:
                continue
            for p in footer.paragraphs:
                if re.search(r"共\s*\d+\s*页", p.text):
                    warnings.append(f"{tag}页脚存在硬编码总页数「{p.text.strip()}」，生成时将修正为动态页数")

    # 去重（同段落同标签）
    seen, uniq = set(), []
    for f in fields:
        key = (f["field_label"], f["fill_op"], str(sorted(f["anchor"].items())))
        if key not in seen:
            seen.add(key)
            uniq.append(f)
    # 同名标签消歧（如笔录 P4/P5 两个“工作单位”→ 询问人工作单位 / 记录人工作单位）
    uniq = _disambiguate(uniq)
    return uniq, warnings


def _disambiguate(fields: list[dict]) -> list[dict]:
    from collections import Counter
    counts = Counter(f["field_label"] for f in fields)
    prev_by_p: dict[int, str] = {}
    for f in fields:
        label = f["field_label"]
        p_idx = f["anchor"].get("p_index")
        if counts[label] > 1:
            prev = prev_by_p.get(p_idx, "")
            prefix = re.sub(r"[（(]签名[）)]", "", prev)
            f["field_label"] = f"{prefix}{label}" if prefix else label
        if p_idx is not None:
            prev_by_p[p_idx] = f["field_label"]
    return fields


def _recognize_table(tbl: Table, ti: int, fields: list[dict]):
    """表格：①标签-值对（空单元格 + 左/上邻接中文标签）；②单元格内 □ 复选框组。"""
    grid: list[list[_Cell]] = tbl.rows and [[c for c in row.cells] for row in tbl.rows] or []
    seen_checkbox_cells: set[int] = set()
    for ri, row in enumerate(tbl.rows):
        cells = list(row.cells)
        for ci, cell in enumerate(cells):
            if ci > 0 and cells[ci - 1] is cell:
                continue  # 横向合并重复对象
            for pi, para in enumerate(cell.paragraphs):
                if CHECKBOX in para.text and id(cell._tc) not in seen_checkbox_cells:
                    seen_checkbox_cells.add(id(cell._tc))
                    # 聚合整个单元格内所有段落的 □ 选项（记录表 R0 有 4 段 7 项）
                    choices = []
                    for pp in cell.paragraphs:
                        choices += [m.group(1).strip() for m in re.finditer(r"□\s*([^□]+?)(?=□|$)", pp.text)]
                    fields.append({"field_label": "检查类型", "fill_op": "checkbox",
                                   "anchor": {"kind": "cell_checkbox", "table": ti, "row": ri, "col": ci,
                                              "choices": choices},
                                   "confidence": "high", "options": {"choices": choices},
                                   "shared": False, "per_frame": False})
            if _clean(cell.text):
                continue
            label = None
            if ci > 0:
                left = cells[ci - 1]
                cand = _clean(left.text).rstrip("：:")
                if cand and _is_label(cand):
                    label = _norm_label(cand)
            if label is None and ri > 0:
                above = grid[ri - 1][ci] if ci < len(grid[ri - 1]) else None
                if above is not None:
                    cand = _clean(above.text).rstrip("：:")
                    if cand and _is_label(cand):
                        label = _norm_label(cand)
            if label:
                fields.append({"field_label": label, "fill_op": "cell_write",
                               "anchor": {"kind": "cell", "table": ti, "row": ri, "col": ci},
                               "confidence": "high", "options": {},
                               "shared": False, "per_frame": False})


def _match_skeleton(text: str, p_index: int) -> list[dict]:
    """形态 E 骨架：编号 / 日期 / 次数 / 起止时间。值用 | 分隔按空位顺序填充。"""
    out = []

    def add(label, pattern, confidence="medium"):
        out.append({"field_label": label, "fill_op": "skeleton",
                    "anchor": {"kind": "paragraph", "p_index": p_index, "pattern": pattern},
                    "confidence": confidence, "options": {},
                    "shared": label in SHARED_LABELS, "per_frame": False})

    stripped = text.strip()
    # 编号：[    ]第      号
    if re.search(r"[：:]\s*\[[\s　]*\]\s*第[\s　]*号", stripped) or re.fullmatch(r"\[[\s　]*\]\s*第[\s　]*号", stripped):
        add("编号", r"\[[\s　]*\]\s*第[\s　]*号")
        return out
    # 日期骨架：年 月 日
    if re.search(r"年[\s　]+月[\s　]+日|[：:][\s　]+年[\s　]*月[\s　]*日", stripped):
        m = LABEL_RE.search(stripped)
        label = _norm_label(m.group(1)) if m and m.start() < 8 else "日期"
        add(label, r"[\s　]*年[\s　]*月[\s　]*日")
        return out
    # 起止时间：时间：……至
    if re.search(r"至\s*$", stripped) and LABEL_RE.search(stripped):
        m = LABEL_RE.search(stripped)
        add(_norm_label(m.group(1)) + "（起止）", r"[：:][\s　]*至[\s　]*$")
        return out
    # 第 X 次（无冒号标签）
    if re.search(r"第[\s　]+次", stripped):
        add("询问次数", r"第[\s　]+次")
        return out
    return out
