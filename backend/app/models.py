from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def now_utc() -> datetime:
    return datetime.now(UTC)


class UserRole(StrEnum):
    ADMIN = "admin"
    INSPECTOR = "inspector"


class InspectionStatus(StrEnum):
    OPEN = "open"
    ARCHIVED = "archived"


class MaterialKind(StrEnum):
    VIDEO = "video"
    IMAGE = "image"
    AUDIO = "audio"
    TEXT = "text"
    OTHER = "other"


class StepKind(StrEnum):
    METADATA = "metadata"
    TRANSCRIPTION = "transcription"
    KEYFRAMES = "keyframes"
    OCR_VISION = "ocr_vision"
    FACT_EXTRACTION = "fact_extraction"
    PARSE = "parse"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    TEMPLATE_ANALYSIS = "template_analysis"
    DOCUMENT_RENDER = "document_render"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class FactStatus(StrEnum):
    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    DELETED = "deleted"


class EvidenceType(StrEnum):
    MEDIA_RANGE = "media_range"
    IMAGE = "image"
    TRANSCRIPT_RANGE = "transcript_range"
    KNOWLEDGE_EXCERPT = "knowledge_excerpt"
    USER_NOTE = "user_note"


class KnowledgeCategory(StrEnum):
    LAW = "law"
    STANDARD = "standard"
    LOCAL_RULE = "local_rule"
    INTERNAL_POLICY = "internal_policy"
    INSPECTION_GUIDE = "inspection_guide"
    OTHER = "other"


class DocumentType(StrEnum):
    INSPECTION_RECORD = "inspection_record"
    PHOTO_DOCUMENT = "photo_document"
    INTERVIEW_TRANSCRIPT = "interview_transcript"


class DocumentStatus(StrEnum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"


class ValueSource(StrEnum):
    GENERATED = "generated"
    HUMAN = "human"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now_utc, onupdate=now_utc
    )


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class VersionMixin:
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class User(Base, IdMixin, TimestampMixin):
    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.INSPECTOR)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class AuthSession(Base, IdMixin):
    __tablename__ = "auth_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
    user: Mapped[User] = relationship()


class Inspection(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "inspections"

    title: Mapped[str] = mapped_column(String(240))
    unit_name: Mapped[str | None] = mapped_column(String(240))
    address: Mapped[str | None] = mapped_column(String(500))
    inspected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[InspectionStatus] = mapped_column(
        Enum(InspectionStatus), default=InspectionStatus.OPEN
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    dynamic_attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    owner: Mapped[User] = relationship()


class InspectionMember(Base):
    __tablename__ = "inspection_members"
    __table_args__ = (UniqueConstraint("inspection_id", "user_id"),)

    inspection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("inspections.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class UploadSession(Base, IdMixin, TimestampMixin):
    __tablename__ = "upload_sessions"

    inspection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inspections.id"))
    filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(200))
    expected_size: Mapped[int] = mapped_column(BigInteger)
    expected_sha256: Mapped[str | None] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(1000))
    external_upload_id: Mapped[str] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(40), default="created")
    parts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class Material(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "materials"

    inspection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inspections.id"), index=True)
    filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(200))
    kind: Mapped[MaterialKind] = mapped_column(Enum(MaterialKind))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    object_key: Mapped[str] = mapped_column(String(1000), unique=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProcessingStep(Base, IdMixin, TimestampMixin):
    __tablename__ = "processing_steps"
    __table_args__ = (UniqueConstraint("subject_type", "subject_id", "kind", "algorithm_version"),)

    subject_type: Mapped[str] = mapped_column(String(40), index=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    kind: Mapped[StepKind] = mapped_column(Enum(StepKind))
    algorithm_version: Mapped[str] = mapped_column(String(40), default="v1")
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus), default=ProcessingStatus.PENDING
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    artifact: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class TranscriptSegment(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "transcript_segments"

    material_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("materials.id"), index=True)
    start_ms: Mapped[int] = mapped_column(Integer)
    end_ms: Mapped[int] = mapped_column(Integer)
    speaker_label: Mapped[str] = mapped_column(String(80), default="说话人 1")
    speaker_identity: Mapped[str | None] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    identity_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class Evidence(Base, IdMixin, TimestampMixin):
    __tablename__ = "evidence"

    inspection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inspections.id"), index=True)
    type: Mapped[EvidenceType] = mapped_column(Enum(EvidenceType))
    material_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("materials.id"))
    transcript_segment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transcript_segments.id")
    )
    start_ms: Mapped[int | None] = mapped_column(Integer)
    end_ms: Mapped[int | None] = mapped_column(Integer)
    object_key: Mapped[str | None] = mapped_column(String(1000))
    knowledge_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge_document_versions.id")
    )
    locator: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    label: Mapped[str] = mapped_column(String(300))
    quote: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Fact(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "facts"

    inspection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inspections.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[FactStatus] = mapped_column(Enum(FactStatus), default=FactStatus.CANDIDATE)
    confidence: Mapped[float | None] = mapped_column(Float)
    source: Mapped[ValueSource] = mapped_column(Enum(ValueSource))
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    manually_edited: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FactEvidenceLink(Base):
    __tablename__ = "fact_evidence_links"
    __table_args__ = (UniqueConstraint("fact_id", "evidence_id"),)

    fact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("facts.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )


class KnowledgeDocument(Base, IdMixin, TimestampMixin):
    __tablename__ = "knowledge_documents"

    title: Mapped[str] = mapped_column(String(500))
    category: Mapped[KnowledgeCategory] = mapped_column(Enum(KnowledgeCategory))
    status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeDocumentVersion(Base, IdMixin, TimestampMixin):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_number"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_documents.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str] = mapped_column(String(200))
    object_key: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus), default=ProcessingStatus.PENDING
    )
    error_message: Mapped[str | None] = mapped_column(Text)


class KnowledgeChunk(Base, IdMixin):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (Index("ix_knowledge_chunks_version_anchor", "version_id", "anchor"),)

    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_document_versions.id"))
    anchor: Mapped[str] = mapped_column(String(200))
    page_number: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(32).with_variant(JSON(), "sqlite"), nullable=True
    )


class Template(Base, IdMixin, TimestampMixin):
    __tablename__ = "templates"

    name: Mapped[str] = mapped_column(String(300))
    document_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType))
    is_public: Mapped[bool] = mapped_column(Boolean, default=True)
    active_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class TemplateVersion(Base, IdMixin, TimestampMixin):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version_number"),)

    template_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("templates.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    original_filename: Mapped[str] = mapped_column(String(500))
    original_object_key: Mapped[str] = mapped_column(String(1000))
    normalized_object_key: Mapped[str] = mapped_column(String(1000))
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus), default=ProcessingStatus.PENDING
    )
    mapping_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    preview_object_key: Mapped[str | None] = mapped_column(String(1000))
    error_message: Mapped[str | None] = mapped_column(Text)


class TemplateRegion(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "template_regions"
    __table_args__ = (UniqueConstraint("template_version_id", "path"),)

    template_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("template_versions.id"))
    path: Mapped[str] = mapped_column(String(500))
    label: Mapped[str] = mapped_column(String(300))
    field_key: Mapped[str] = mapped_column(String(300))
    field_type: Mapped[str] = mapped_column(String(40), default="text")
    source_path: Mapped[str | None] = mapped_column(String(500))
    fill_mode: Mapped[str] = mapped_column(String(40), default="replace")
    repeatable: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    region_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class DocumentDraft(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "document_drafts"

    inspection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("inspections.id"), index=True)
    template_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("template_versions.id"))
    document_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType))
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), default=DocumentStatus.DRAFT
    )
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class DocumentField(Base, IdMixin, TimestampMixin, VersionMixin):
    __tablename__ = "document_fields"
    __table_args__ = (UniqueConstraint("document_id", "region_id"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_drafts.id"))
    region_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("template_regions.id"))
    field_key: Mapped[str] = mapped_column(String(300))
    value: Mapped[Any] = mapped_column(JSON)
    source: Mapped[ValueSource] = mapped_column(Enum(ValueSource), default=ValueSource.GENERATED)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)


class DocumentVersion(Base, IdMixin, TimestampMixin):
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_number"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_drafts.id"))
    version_number: Mapped[int] = mapped_column(Integer)
    fields_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    object_key: Mapped[str | None] = mapped_column(String(1000))
    preview_object_key: Mapped[str | None] = mapped_column(String(1000))
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)


class AssistantConversation(Base, IdMixin, TimestampMixin):
    __tablename__ = "assistant_conversations"

    inspection_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inspections.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(300), default="新对话")


class AssistantMessage(Base, IdMixin):
    __tablename__ = "assistant_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("assistant_conversations.id"))
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    page_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)


class AuditEvent(Base, IdMixin):
    __tablename__ = "audit_events"

    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    subject_type: Mapped[str] = mapped_column(String(80))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)
