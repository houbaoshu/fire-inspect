"""种子数据（PRD §16 / 澄清 §4）：测试账号 + 消防法条款级入库 + 预置三份模板。

用法：.venv/bin/python -m app.seed [--reset]
  --reset：清库 + 清文件卷后重建（验收环境初始化）
"""
import sys, shutil
from pathlib import Path
from .core.db import engine, SessionLocal, Base, init_db
from .core.config import ROOT, MEDIA_DIR, EXPORT_DIR, settings
from .core.models import User, KnowledgeDoc, Template, TemplateField
from .core.security import hash_password
from .domain import knowledge, template_recognize

if settings.seed_dir:
    KNOWLEDGE_DIR = Path(settings.seed_dir) / "knowledge"   # 容器：/seed/knowledge
    TEMPLATE_DIR = Path(settings.seed_dir) / "templates"    # 容器：/seed/templates
else:
    KNOWLEDGE_DIR = ROOT / "testdata" / "knowledge"
    TEMPLATE_DIR = ROOT / "docs"
FIRE_LAW_PDF = KNOWLEDGE_DIR / "reg_ec997c582c9c29a6.pdf"
BUILTIN_TEMPLATES = [
    ("监督检查记录表", "record", TEMPLATE_DIR / "监督检查记录表.docx"),
    ("照片文书", "photo", TEMPLATE_DIR / "照片.docx"),
    ("询问笔录", "interrogation", TEMPLATE_DIR / "询问笔录.docx"),
]


def seed_users(db):
    if not db.query(User).filter_by(username="admin").first():
        db.add(User(username="admin", password_hash=hash_password("admin123"), role="admin"))
    if not db.query(User).filter_by(username="inspector").first():
        db.add(User(username="inspector", password_hash=hash_password("inspector123"), role="inspector"))
    db.commit()
    print("✓ 测试账号：admin / inspector")


def seed_fire_law(db):
    if db.query(KnowledgeDoc).filter_by(builtin=True).first():
        print("✓ 消防法已预置，跳过")
        return
    if not FIRE_LAW_PDF.exists():
        print("⚠️ 消防法 PDF 不存在，跳过：", FIRE_LAW_PDF)
        return
    rel = Path("media") / "knowledge" / "fire_law.pdf"
    dest = MEDIA_DIR.parent / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(FIRE_LAW_PDF, dest)
    d = KnowledgeDoc(title="中华人民共和国消防法", doc_type="正式法律法规", filename=FIRE_LAW_PDF.name,
                     file_path=str(rel), status="processing", builtin=True)
    db.add(d)
    db.commit()
    knowledge.ingest_doc(db, d.id)
    db.commit()
    n = db.query(knowledge.KnowledgeChunk).filter_by(doc_id=d.id).count()
    print(f"✓ 消防法入库：{d.status}，{n} 个条款块", ("⚠️ " + d.error) if d.error else "")


def seed_templates(db):
    for name, doc_type, src in BUILTIN_TEMPLATES:
        if db.query(Template).filter_by(name=name, builtin=True).first():
            continue
        if not src.exists():
            print(f"⚠️ 模板缺失：{src}")
            continue
        rel = Path("media") / "templates" / f"builtin_{doc_type}.docx"
        dest = MEDIA_DIR.parent / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)
        t = Template(name=name, doc_type=doc_type, filename=src.name, file_path=str(rel),
                     builtin=True, status="ready")
        db.add(t)
        db.flush()
        fields, warnings = template_recognize.recognize(str(dest), doc_type)
        for f in fields:
            # 预置模板经 spike 验证（TEMPLATE-ANALYSIS §3），全部字段直接确认
            db.add(TemplateField(template_id=t.id, **f, confirmed=True))
        t.warnings = warnings
        db.commit()
        print(f"✓ 预置模板「{name}」：识别 {len(fields)} 字段，警告 {len(warnings)} 条")


def reset():
    """清库 + 清文件卷（仅开发/验收环境）。"""
    Base.metadata.drop_all(engine)
    init_db()
    for d in (MEDIA_DIR, EXPORT_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    print("✓ 已重置数据库与文件卷")


def main():
    init_db()
    if "--reset" in sys.argv:
        reset()
    db = SessionLocal()
    try:
        seed_users(db)
        seed_fire_law(db)
        seed_templates(db)
    finally:
        db.close()
    print("种子数据完成")


if __name__ == "__main__":
    main()
