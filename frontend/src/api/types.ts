export type Role = "inspector" | "admin";

export interface User {
  id: string;
  username: string;
  role: Role;
  status: "active" | "disabled";
  display_name?: string;
  created_at?: string;
  updated_at?: string;
}

export type JobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "superseded";

export interface JobSummary {
  id: string;
  task_type: string;
  status: JobStatus;
  safe_error_message?: string | null;
  attempt_count?: number;
  max_attempts?: number;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  scope_label?: string;
  inspection_id?: string;
}

export interface ProcessingSummary {
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
}

export interface Inspection {
  id: string;
  unit_name: string;
  unit_address: string;
  responsible_person?: string;
  contact_name?: string;
  contact_phone?: string;
  inspection_at: string;
  inspection_type: string;
  general_notes?: string;
  owner: User;
  personnel?: Array<{ id?: string; name: string; work_unit?: string; role?: string }>;
  shared_revision?: number;
  revision?: number;
  processing_summary?: ProcessingSummary;
  created_at?: string;
  updated_at?: string;
}

export interface MaterialStep {
  name: string;
  label?: string;
  status: JobStatus | "not_started";
  job_id?: string;
  safe_error_message?: string | null;
}

export interface Material {
  id: string;
  inspection_id: string;
  kind: "video" | "image" | "audio" | "text" | string;
  display_name: string;
  mime_type?: string;
  byte_size?: number;
  duration_ms?: number;
  revision?: number;
  created_at?: string;
  steps?: MaterialStep[] | Record<string, MaterialStep | JobStatus>;
  playback_url?: string;
  thumbnail_url?: string;
}

export interface TranscriptSegment {
  id: string;
  sequence: number;
  start_ms: number;
  end_ms: number;
  text: string;
  suggested_role: "question" | "answer" | "unknown";
  confirmed_role?: "question" | "answer" | null;
  text_origin?: "ai" | "manual";
  revision?: number;
}

export interface Transcript {
  id: string;
  material_id: string;
  material_name?: string;
  full_text: string;
  full_text_origin?: "ai" | "manual";
  language?: string;
  revision?: number;
  segments: TranscriptSegment[];
  updated_at?: string;
}

export type FactReviewState = "candidate" | "confirmed" | "withdrawn";

export interface FactEvidence {
  id?: string;
  type: "media_range" | "transcript" | "keyframe" | "photo" | "clause";
  label?: string;
  material_id?: string;
  start_ms?: number;
  end_ms?: number;
  key_frame_id?: string;
  photo_material_id?: string;
  segment_id?: string;
  document_id?: string;
  clause_id?: string;
  clause_identifier?: string;
}

export interface Fact {
  id: string;
  inspection_id: string;
  content: string;
  review_state: FactReviewState;
  needs_verification: boolean;
  verification_note?: string;
  origin: "ai" | "manual";
  revision?: number;
  evidence?: FactEvidence[];
  created_at?: string;
  updated_at?: string;
}

export interface KnowledgeSearchResult {
  id: string;
  clause_identifier?: string;
  heading?: string;
  content: string;
  applicability_reason?: string;
  document: {
    id: string;
    title: string;
    category?: string;
    source_verification?: "pending" | "verified" | "rejected";
    validity?: string;
    issuer?: string;
  };
  page_start?: number;
}

export interface DocumentField {
  key: string;
  display_name: string;
  value: unknown;
  origin?: "ai" | "manual" | "system";
  manual_locked?: boolean;
  shared?: boolean;
}

export type DocumentType = "record" | "photo" | "interview";

export interface InspectionDocument {
  id: string;
  inspection_id: string;
  document_type: DocumentType;
  name: string;
  state: "draft" | "confirmed";
  revision?: number;
  fields?: DocumentField[] | Record<string, unknown>;
  invalidation_reason?: string;
  generator_status?: JobStatus | "idle";
  generator_job_id?: string;
  safe_error_message?: string | null;
  generator_safe_error?: string | null;
  generation_valid?: boolean;
  generated_page_count?: number | null;
  upgrade_available?: boolean;
  fact_dependency_ids?: string[];
  photo_items?: Array<{
    id: string;
    sequence: number;
    thumbnail_url?: string;
    source_label?: string;
    reflected_content?: string;
    reflected_content_origin?: "ai" | "manual";
    reflected_content_manual_locked?: boolean;
    description?: string;
    description_origin?: "ai" | "manual";
    description_manual_locked?: boolean;
  }>;
  qa_items?: Array<{
    id: string;
    sequence: number;
    text: string;
    suggested_role?: "question" | "answer" | "unknown";
    confirmed_role?: "question" | "answer" | null;
    content_origin?: "ai" | "manual";
    content_manual_locked?: boolean;
  }>;
  updated_at?: string;
}

export interface HistoryEntry {
  id: string;
  object_type: string;
  object_label?: string;
  action: string;
  actor_label?: string;
  created_at?: string;
  occurred_at?: string;
  old_value?: unknown;
  new_value?: unknown;
  old_values?: unknown;
  new_values?: unknown;
  snapshot?: unknown;
  actor?: string;
  reason?: string;
  masked_change_summary?: string;
}

export interface AssistantCitation {
  source_type: string;
  source_id: string;
  excerpt: string;
}

export type AssistantNavigationAction =
  | {
      kind: "material_time";
      target_id: string;
      inspection_id: string;
      material_id: string;
      start_ms: number;
    }
  | {
      kind: "photo";
      target_id: string;
      inspection_id: string;
      photo_id: string;
      material_id?: null;
      start_ms?: null;
    }
  | {
      kind: "fact";
      target_id: string;
      inspection_id: string;
      fact_id: string;
      material_id?: null;
      start_ms?: null;
    }
  | {
      kind: "clause";
      target_id: string;
      inspection_id: string;
      clause_id: string;
      document_id: string;
      material_id?: null;
      start_ms?: null;
    }
  | {
      kind: "document";
      target_id: string;
      inspection_id: string;
      document_id: string;
      document_type: DocumentType;
      material_id?: null;
      start_ms?: null;
    };

export interface AssistantResponse {
  text: string;
  citations: AssistantCitation[];
  navigation: AssistantNavigationAction[];
  read_only: boolean;
}

export interface PaginatedResponse<T> {
  count?: number;
  next?: string | null;
  previous?: string | null;
  results: T[];
}

export function asList<T>(value: T[] | PaginatedResponse<T> | null | undefined): T[] {
  if (Array.isArray(value)) return value;
  if (value && Array.isArray(value.results)) return value.results;
  return [];
}

export function historyActor(entry: HistoryEntry): string {
  return entry.actor_label || entry.actor || "系统";
}

export function historyOldValue(entry: HistoryEntry): unknown {
  return entry.old_value !== undefined ? entry.old_value : entry.old_values;
}

export function historyNewValue(entry: HistoryEntry): unknown {
  if (entry.new_value !== undefined) return entry.new_value;
  if (entry.new_values !== undefined) return entry.new_values;
  return entry.snapshot;
}
