"""知识库：文档解析（docx/pdf）、条款级切分、向量化、检索问答（PLAN 阶段 3）。

- 消防法预置：条款级切分（第 X 条），标记"正式法律法规"（澄清 §4）；
- 所有引用可追溯：chunk 带文档 + 条款标签；
- 无召回时明确告知，禁止伪造条款（PRD §5）。
"""
import re
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text
from ..core.config import MEDIA_DIR, settings
from ..core.models import KnowledgeDoc, KnowledgeChunk
from ..core import ai

DOC_TYPES = ["正式法律法规", "标准规范", "地方规定", "内部制度", "其他"]

CLAUSE_RE = re.compile(r"(第[一二三四五六七八九十百千零〇0-9]+条(?:之[一二三四五六七八九十])?)")


def _parse_docx(path: str) -> str:
    import docx
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs if p.text.strip()]
    for tbl in d.tables:
        for row in tbl.rows:
            for cell in row.cells:
                if cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


def _parse_pdf(path: str) -> str:
    import pymupdf
    doc = pymupdf.open(path)
    return "\n".join(page.get_text() for page in doc)


def parse_text(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".docx":
        return _parse_docx(path)
    if ext == ".pdf":
        return _parse_pdf(path)
    if ext in (".txt", ".md"):
        return Path(path).read_text(encoding="utf-8")
    raise ValueError(f"暂不支持的文档格式：{ext}（支持 docx/pdf/txt/md；扫描件请转文字版后上传）")


def split_clauses(full_text: str) -> list[tuple[str, str]]:
    """条款级切分：以"第 X 条"为锚。无条款锚的文档按段落聚合。"""
    lines = [ln.strip() for ln in full_text.splitlines()]
    chunks: list[tuple[str, str]] = []
    cur_label, cur = "", []
    for ln in lines:
        if not ln:
            continue
        m = CLAUSE_RE.search(ln)
        if m and (m.start() < 10):  # 条款号出现在行首附近才视为新条款
            if cur:
                chunks.append((cur_label, "\n".join(cur)))
            cur_label, cur = m.group(1), [ln]
        else:
            cur.append(ln)
    if cur:
        chunks.append((cur_label, "\n".join(cur)))
    # 合并过短碎片到前一条（如标题、目次）
    merged: list[tuple[str, str]] = []
    for label, body in chunks:
        if merged and len(body) < 30 and not label:
            merged[-1] = (merged[-1][0], merged[-1][1] + "\n" + body)
        else:
            merged.append((label, body))
    # 超长切块（embedding 输入限制）
    out: list[tuple[str, str]] = []
    for label, body in merged:
        while len(body) > 1200:
            out.append((label, body[:1200]))
            body = body[1200:]
        out.append((label, body))
    return out


def ingest_doc(db: Session, doc_id: int):
    doc = db.get(KnowledgeDoc, doc_id)
    try:
        full = parse_text(str(MEDIA_DIR.parent / doc.file_path))
        clauses = [(lb, tx) for lb, tx in split_clauses(full) if tx.strip()]
        if not clauses:
            raise ValueError("未能从文档中解析出文本内容")
        db.query(KnowledgeChunk).filter(KnowledgeChunk.doc_id == doc_id).delete()
        db.flush()
        vectors = ai.embed_texts([tx for _, tx in clauses])
        for seq, ((label, body), vec) in enumerate(zip(clauses, vectors)):
            db.add(KnowledgeChunk(doc_id=doc_id, seq=seq, clause_label=label, text=body, embedding=vec))
        doc.status, doc.error = "ready", ""
    except Exception as e:  # 失败不丢原始文档（PRD §14）
        doc.status, doc.error = "failed", str(e)[:500]
    db.flush()


def retrieve(db: Session, question: str, top_k: int = 6) -> list[dict]:
    """向量召回，返回带出处（文档标题 + 条款标签）的 chunk。"""
    if not settings.ai_available("embed"):
        raise ai.AIError("Embedding 端点未配置，无法检索")
    vec = ai.embed_texts([question])[0]
    rows = db.execute(sql_text("""
        SELECT c.id, c.clause_label, c.text, d.title, d.doc_type,
               (c.embedding <=> CAST(:v AS vector)) AS distance
        FROM knowledge_chunks c JOIN knowledge_docs d ON d.id = c.doc_id
        WHERE d.status = 'ready' AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> CAST(:v AS vector) LIMIT :k
    """), {"v": str(vec), "k": top_k}).all()
    return [{"chunk_id": r.id, "clause_label": r.clause_label, "text": r.text,
             "doc_title": r.title, "doc_type": r.doc_type, "distance": float(r.distance)} for r in rows]


ANSWER_SYSTEM = """你是消防法规检索助手。只能依据"检索到的条款原文"回答，禁止编造任何条款、编号或来源。
要求：
1. 若检索内容与问题无关或不足以回答，直接回答"知识库中未找到相关规定"，不要推测。
2. 回答包含：可能涉及的问题、相关条款（注明文档与条款号）、简要适用理由。
3. 引用条款时用 [条款n] 标记，对应输入中的编号。
输出 JSON：{"found": true/false, "issues": ["..."], "answer": "...", "cited": [1, 2]}"""


def query(db: Session, question: str, top_k: int = 6) -> dict:
    chunks = retrieve(db, question, top_k)
    if not chunks:
        return {"found": False, "issues": [], "answer": "知识库为空，请先上传知识文档。", "sources": []}
    ctx = "\n\n".join(f"[条款{i+1}] 《{c['doc_title']}》（{c['doc_type']}）{c['clause_label']}\n{c['text']}"
                      for i, c in enumerate(chunks))
    try:
        r = ai.llm_json(ANSWER_SYSTEM, f"问题：{question}\n\n检索到的条款原文：\n{ctx}")
    except ai.AIError as e:
        return {"found": None, "issues": [], "answer": f"AI 组织答案失败（{e}），以下为原始检索结果。",
                "sources": chunks}
    cited = r.get("cited") or []
    sources = [chunks[i - 1] for i in cited if isinstance(i, int) and 1 <= i <= len(chunks)] or chunks
    return {"found": bool(r.get("found")), "issues": r.get("issues") or [],
            "answer": r.get("answer") or "", "sources": sources}
