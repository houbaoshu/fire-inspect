from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text

from app.core.config import get_settings
from app.core.database import Base, SessionLocal, engine
from app.core.security import hash_password
from app.core.storage import get_storage
from app.models import DocumentTemplate, KnowledgeDocument, User, new_id
from app.modules import assets, auth, documents, facts, inspections, jobs, knowledge
from app.services.documents import default_field_manifest, scan_template_fields
from app.services.knowledge import index_document


def seed_data() -> None:
    settings = get_settings()
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.username == settings.bootstrap_admin_username))
        if not admin:
            db.add(
                User(
                    username=settings.bootstrap_admin_username,
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    role="admin",
                )
            )
            db.commit()
            admin = db.scalar(
                select(User).where(User.username == settings.bootstrap_admin_username)
            )
        template_types = {
            "inspection-record.docx": ("消防监督检查记录", "inspection_record"),
            "photo-record.docx": ("照片类文书", "photo_record"),
            "inquiry-record.docx": ("询问笔录", "inquiry_record"),
        }
        for filename, (name, document_type) in template_types.items():
            path = settings.sample_templates_dir / filename
            if not path.exists():
                continue
            key = f"templates/builtin/{filename}"
            existing_template = db.scalar(
                select(DocumentTemplate).where(DocumentTemplate.storage_key == key)
            )
            if existing_template:
                if document_type == "photo_record":
                    fields = [dict(item) for item in existing_template.fields_json]
                    changed = False
                    for field in fields:
                        if field.get("code") == "photo" and field.get("type") == "image":
                            field["type"] = "photo_list"
                            field["description"] = "选择一张或多张证据照片或关键帧"
                            changed = True
                    if changed:
                        existing_template.fields_json = fields
                continue
            content = path.read_bytes()
            get_storage().put_bytes(
                key,
                content,
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            codes = scan_template_fields(content)
            db.add(
                DocumentTemplate(
                    id=new_id(),
                    owner_id=None,
                    name=name,
                    document_type=document_type,
                    storage_key=key,
                    fields_json=default_field_manifest(codes),
                    builtin=True,
                )
            )
        db.commit()
        reference_path = settings.sample_templates_dir / "knowledge-reference.txt"
        reference_key = "knowledge/builtin/knowledge-reference.txt"
        if reference_path.exists() and not db.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.storage_key == reference_key)
        ):
            get_storage().put_bytes(reference_key, reference_path.read_bytes(), "text/plain")
            reference = KnowledgeDocument(
                title="消防安全业务演示参考材料",
                filename=reference_path.name,
                content_type="text/plain",
                material_type="reference",
                storage_key=reference_key,
                uploaded_by=admin.id,
            )
            db.add(reference)
            db.commit()
            try:
                index_document(db, reference.id)
            except Exception:
                # 保留失败状态供管理员查看和重试，不阻塞系统启动。
                pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    if engine.dialect.name == "postgresql":
        with engine.begin() as connection:
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(engine)
    seed_data()
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in [
    auth.router,
    auth.users_router,
    inspections.router,
    assets.router,
    facts.router,
    knowledge.router,
    documents.router,
    jobs.router,
]:
    app.include_router(router, prefix=settings.api_prefix)


@app.get("/health")
def health():
    return {"status": "ok"}
