"""数据模型（PLAN §1.4 逻辑视图落地）。"""
from datetime import datetime, timezone
from sqlalchemy import (String, Text, Integer, BigInteger, Boolean, DateTime,
                        ForeignKey, JSON, Index)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector
from .db import Base
from .config import settings


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="inspector")  # inspector / admin
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Session(Base):
    __tablename__ = "sessions"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Inspection(Base):
    __tablename__ = "inspections"
    id: Mapped[int] = mapped_column(primary_key=True)
    unit_name: Mapped[str] = mapped_column(String(256), default="")
    address: Mapped[str] = mapped_column(String(512), default="")
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checker_names: Mapped[str] = mapped_column(String(256), default="")   # 检查人员
    contact: Mapped[str] = mapped_column(String(256), default="")         # 联系人及电话
    legal_person: Mapped[str] = mapped_column(String(128), default="")    # 法定代表/主要负责人
    check_type: Mapped[str] = mapped_column(String(64), default="")       # 检查类型（记录表打勾项）
    notes: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16))  # video / audio / photo / other
    filename: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str] = mapped_column(String(1024))  # 相对 data_dir
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    """处理任务状态机：pending → running → succeeded / failed（可重试）。"""
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int | None] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), nullable=True, index=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32))  # transcribe_full / transcribe_windows / keyframes / fact_extract / vl_caption / knowledge_ingest
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending/running/succeeded/failed
    error: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Transcript(Base):
    __tablename__ = "transcripts"
    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # full / window
    job_id: Mapped[int | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    segments: Mapped[list["TranscriptSegment"]] = relationship(back_populates="transcript", cascade="all, delete-orphan")
    material: Mapped[Material] = relationship()


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    transcript_id: Mapped[int] = mapped_column(ForeignKey("transcripts.id", ondelete="CASCADE"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    start_ms: Mapped[int] = mapped_column(Integer, default=0)
    end_ms: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    speaker: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 预留扩展位（澄清 §6）
    qa_role: Mapped[str | None] = mapped_column(String(8), nullable=True)   # 问 / 答 / None=未标
    qa_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    transcript: Mapped[Transcript] = relationship(back_populates="segments")


class Keyframe(Base):
    __tablename__ = "keyframes"
    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True)
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), nullable=True, index=True)
    ts_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None=用户上传照片
    image_path: Mapped[str] = mapped_column(String(1024))
    quality_score: Mapped[float] = mapped_column(default=0.0)
    phash: Mapped[str] = mapped_column(String(64), default="")
    caption: Mapped[str] = mapped_column(Text, default="")  # VL 画面描述
    source: Mapped[str] = mapped_column(String(16), default="extracted")  # extracted / uploaded
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)  # 用户删除错误候选
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Fact(Base):
    __tablename__ = "facts"
    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="candidate")  # candidate / confirmed
    source: Mapped[str] = mapped_column(String(16), default="ai")  # ai / user
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    evidences: Mapped[list["FactEvidence"]] = relationship(back_populates="fact", cascade="all, delete-orphan")


class FactEvidence(Base):
    """事实↔证据纽带；material_id 冗余存储，防止多文件时跳错文件（PRD §11）。"""
    __tablename__ = "fact_evidences"
    id: Mapped[int] = mapped_column(primary_key=True)
    fact_id: Mapped[int] = mapped_column(ForeignKey("facts.id", ondelete="CASCADE"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(24))  # video_segment / keyframe / transcript_segment / knowledge_chunk
    material_id: Mapped[int | None] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), nullable=True)
    ref: Mapped[dict] = mapped_column(JSON, default=dict)  # {start_ms,end_ms} / {keyframe_id} / {segment_id} / {chunk_id}
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fact: Mapped[Fact] = relationship(back_populates="evidences")


class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    doc_type: Mapped[str] = mapped_column(String(32), default="其他")  # 正式法律法规/标准规范/地方规定/内部制度/其他
    filename: Mapped[str] = mapped_column(String(512), default="")
    file_path: Mapped[str] = mapped_column(String(1024), default="")
    status: Mapped[str] = mapped_column(String(16), default="processing")  # processing / ready / failed
    error: Mapped[str] = mapped_column(Text, default="")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("knowledge_docs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    clause_label: Mapped[str] = mapped_column(String(64), default="")  # 如"第十六条"
    text: Mapped[str] = mapped_column(Text)
    embedding = mapped_column(Vector(settings.embed_dim), nullable=True)


Index("ix_chunks_embedding", KnowledgeChunk.embedding, postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"})


class Template(Base):
    __tablename__ = "templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256))
    doc_type: Mapped[str] = mapped_column(String(24))  # record / photo / interrogation
    filename: Mapped[str] = mapped_column(String(512), default="")
    file_path: Mapped[str] = mapped_column(String(1024), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="ready")
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    warnings: Mapped[dict] = mapped_column(JSON, default=list)  # 识别期警告（如硬编码总页数）
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fields: Mapped[list["TemplateField"]] = relationship(back_populates="template", cascade="all, delete-orphan",
                                                         order_by="TemplateField.id")


class TemplateField(Base):
    """模板字段映射：稳定中文业务名，不向用户暴露内部键（PRD §16）。"""
    __tablename__ = "template_fields"
    id: Mapped[int] = mapped_column(primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.id", ondelete="CASCADE"), index=True)
    field_label: Mapped[str] = mapped_column(String(128))      # 中文业务名
    fill_op: Mapped[str] = mapped_column(String(32))           # cell_write/checkbox/append_after_label/replace_spaces/replace_placeholder_image/generate_body
    anchor: Mapped[dict] = mapped_column(JSON, default=dict)   # 定位信息（段落/单元格路径）
    confidence: Mapped[str] = mapped_column(String(8), default="high")  # high / medium / low
    options: Mapped[dict] = mapped_column(JSON, default=dict)  # checkbox 选项等
    shared: Mapped[bool] = mapped_column(Boolean, default=False)    # 同源字段（单次检查只维护一次）
    per_frame: Mapped[bool] = mapped_column(Boolean, default=False)  # 逐帧字段（照片文书）
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    template: Mapped[Template] = relationship(back_populates="fields")


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    inspection_id: Mapped[int] = mapped_column(ForeignKey("inspections.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("templates.id", ondelete="SET NULL"), nullable=True)
    doc_type: Mapped[str] = mapped_column(String(24))  # record / photo / interrogation
    name: Mapped[str] = mapped_column(String(512), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft / confirmed
    file_path: Mapped[str] = mapped_column(String(1024), default="")  # 最近渲染产物
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    fields: Mapped[list["DocumentField"]] = relationship(back_populates="document", cascade="all, delete-orphan",
                                                         order_by="DocumentField.id")


class DocumentField(Base):
    __tablename__ = "document_fields"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    field_label: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(8), default="ai")  # ai / user（PRD §10：不无提示覆盖人工修改）
    per_frame: Mapped[bool] = mapped_column(Boolean, default=False)
    frame_key: Mapped[str] = mapped_column(String(64), default="")  # 逐帧字段归属（keyframe id）
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    document: Mapped[Document] = relationship(back_populates="fields")


class AuditLog(Base):
    """操作留痕：谁/何时/对象/动作/旧值→新值（澄清 §7）。"""
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    action: Mapped[str] = mapped_column(String(32))   # create / update / confirm / status_change / delete ...
    object_type: Mapped[str] = mapped_column(String(32), index=True)  # fact / document / inspection ...
    object_id: Mapped[int] = mapped_column(Integer, index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # {field, old, new} 或描述
