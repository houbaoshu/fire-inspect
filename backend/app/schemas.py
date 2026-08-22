from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import (
    DocumentStatus,
    DocumentType,
    EvidenceType,
    FactStatus,
    InspectionStatus,
    KnowledgeCategory,
    MaterialKind,
    ProcessingStatus,
    StepKind,
    UserRole,
    ValueSource,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORMModel):
    id: uuid.UUID
    username: str
    display_name: str
    role: UserRole
    is_active: bool


class LoginIn(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=200)
    role: UserRole = UserRole.INSPECTOR


class InspectionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    unit_name: str | None = None
    address: str | None = None
    inspected_at: datetime | None = None
    dynamic_attributes: dict[str, Any] = Field(default_factory=dict)


class InspectionUpdate(BaseModel):
    title: str | None = None
    unit_name: str | None = None
    address: str | None = None
    inspected_at: datetime | None = None
    dynamic_attributes: dict[str, Any] | None = None
    status: InspectionStatus | None = None
    row_version: int


class InspectionOut(ORMModel):
    id: uuid.UUID
    title: str
    unit_name: str | None
    address: str | None
    inspected_at: datetime | None
    dynamic_attributes: dict[str, Any]
    status: InspectionStatus
    owner_id: uuid.UUID
    row_version: int
    created_at: datetime
    updated_at: datetime


class MemberUpdate(BaseModel):
    user_ids: list[uuid.UUID]


class UploadStart(BaseModel):
    filename: str = Field(min_length=1, max_length=500)
    content_type: str = "application/octet-stream"
    size_bytes: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class UploadComplete(BaseModel):
    parts: list[dict[str, Any]] = Field(default_factory=list)


class ProcessingStepOut(ORMModel):
    id: uuid.UUID
    kind: StepKind
    status: ProcessingStatus
    algorithm_version: str
    attempt_count: int
    error_code: str | None
    error_message: str | None
    artifact: dict[str, Any]
    updated_at: datetime


class MaterialOut(ORMModel):
    id: uuid.UUID
    inspection_id: uuid.UUID
    filename: str
    content_type: str
    kind: MaterialKind
    size_bytes: int
    sha256: str
    created_at: datetime
    row_version: int
    steps: list[ProcessingStepOut] = Field(default_factory=list)


class TranscriptUpdate(BaseModel):
    speaker_identity: str | None = None
    text: str | None = None
    identity_confirmed: bool | None = None
    row_version: int


class TranscriptOut(ORMModel):
    id: uuid.UUID
    material_id: uuid.UUID
    start_ms: int
    end_ms: int
    speaker_label: str
    speaker_identity: str | None
    text: str
    confidence: float | None
    identity_confirmed: bool
    row_version: int


class EvidenceCreate(BaseModel):
    type: EvidenceType
    material_id: uuid.UUID | None = None
    transcript_segment_id: uuid.UUID | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    object_key: str | None = None
    knowledge_version_id: uuid.UUID | None = None
    locator: dict[str, Any] = Field(default_factory=dict)
    label: str
    quote: str | None = None


class EvidenceOut(ORMModel):
    id: uuid.UUID
    inspection_id: uuid.UUID
    type: EvidenceType
    material_id: uuid.UUID | None
    transcript_segment_id: uuid.UUID | None
    start_ms: int | None
    end_ms: int | None
    object_key: str | None
    knowledge_version_id: uuid.UUID | None
    locator: dict[str, Any]
    label: str
    quote: str | None


class FactCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    detail: str | None = None
    evidence_ids: list[uuid.UUID] = Field(default_factory=list)


class FactUpdate(BaseModel):
    title: str | None = None
    detail: str | None = None
    evidence_ids: list[uuid.UUID] | None = None
    row_version: int


class FactOut(ORMModel):
    id: uuid.UUID
    inspection_id: uuid.UUID
    title: str
    detail: str | None
    status: FactStatus
    confidence: float | None
    source: ValueSource
    needs_confirmation: bool
    manually_edited: bool
    row_version: int
    confirmed_by: uuid.UUID | None
    confirmed_at: datetime | None
    evidence: list[EvidenceOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class KnowledgeOut(ORMModel):
    id: uuid.UUID
    title: str
    category: KnowledgeCategory
    status: str
    created_at: datetime
    latest_version_status: ProcessingStatus | None = None
    steps: list[ProcessingStepOut] = Field(default_factory=list)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=8, ge=1, le=30)


class Citation(BaseModel):
    document_id: uuid.UUID
    version_id: uuid.UUID
    title: str
    category: KnowledgeCategory
    anchor: str
    page_number: int | None
    quote: str


class SearchOut(BaseModel):
    answer: str
    possible_issue: str | None = None
    applicability: str | None = None
    citations: list[Citation]


class TemplateRegionUpdate(BaseModel):
    label: str | None = None
    field_key: str | None = None
    field_type: str | None = None
    source_path: str | None = None
    fill_mode: str | None = None
    repeatable: bool | None = None
    confirmed: bool | None = None
    row_version: int


class TemplateRegionOut(ORMModel):
    id: uuid.UUID
    path: str
    label: str
    field_key: str
    field_type: str
    source_path: str | None
    fill_mode: str
    repeatable: bool
    confidence: float
    confirmed: bool
    ordinal: int
    region_data: dict[str, Any]
    row_version: int


class TemplateOut(ORMModel):
    id: uuid.UUID
    name: str
    document_type: DocumentType
    is_public: bool
    active_version_id: uuid.UUID | None
    created_at: datetime


class TemplateVersionOut(ORMModel):
    id: uuid.UUID
    template_id: uuid.UUID
    version_number: int
    original_filename: str
    status: ProcessingStatus
    mapping_confirmed: bool
    preview_object_key: str | None
    error_message: str | None
    regions: list[TemplateRegionOut] = Field(default_factory=list)


class DocumentCreate(BaseModel):
    template_version_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    selected_evidence_ids: list[uuid.UUID] = Field(default_factory=list)


class DocumentFieldUpdate(BaseModel):
    value: Any
    row_version: int


class DocumentFieldOut(ORMModel):
    id: uuid.UUID
    region_id: uuid.UUID
    field_key: str
    label: str = ""
    field_type: str = "text"
    source_path: str | None = None
    is_shared: bool = False
    is_evidence_linked: bool = False
    value: Any
    source: ValueSource
    is_locked: bool
    is_stale: bool
    row_version: int


class DocumentOut(ORMModel):
    id: uuid.UUID
    inspection_id: uuid.UUID
    template_version_id: uuid.UUID
    document_type: DocumentType
    title: str
    status: DocumentStatus
    row_version: int
    confirmed_by: uuid.UUID | None
    confirmed_at: datetime | None
    fields: list[DocumentFieldOut] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class RegenerateIn(BaseModel):
    field_ids: list[uuid.UUID]


class RegenerateProposal(BaseModel):
    field_id: uuid.UUID
    current_value: Any
    proposed_value: Any
    locked: bool


class RegenerateAccept(BaseModel):
    proposals: list[dict[str, Any]]


class AssistantContext(BaseModel):
    page_type: str
    inspection_id: uuid.UUID | None = None
    document_id: uuid.UUID | None = None
    fact_id: uuid.UUID | None = None
    material_id: uuid.UUID | None = None
    selected_text: str | None = Field(default=None, max_length=4000)


class AssistantIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: uuid.UUID | None = None
    context: AssistantContext


class NavigationAction(BaseModel):
    type: Literal["seek_media", "open_image", "open_knowledge", "open_fact", "open_document"]
    params: dict[str, Any]
    label: str


class AssistantOut(BaseModel):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    content: str
    citations: list[dict[str, Any]]
    actions: list[NavigationAction]
