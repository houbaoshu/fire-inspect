export type User = { id: string; username: string; role: 'admin' | 'inspector'; active: boolean }
export type Inspection = { id: string; owner_id: string; title: string; status: string; extra_fields: Record<string, unknown>; created_at: string; updated_at: string }
export type Asset = { id: string; kind: string; filename: string; processing_status: string; processing_error?: string; metadata_json: Record<string, unknown>; created_at: string }
export type Evidence = { id: string; asset_id: string; kind: string; start_ms?: number; end_ms?: number; excerpt?: string; has_file: boolean }
export type Fact = { id: string; kind: string; summary: string; content: Record<string, unknown>; status: 'candidate' | 'confirmed' | 'rejected'; source: 'ai' | 'user'; confidence?: number; evidence_ids: string[]; updated_at: string }
export type Template = { id: string; name: string; document_type: string; fields_json: { code: string; label: string; type: string; description: string }[]; builtin: boolean }
export type FieldState = { value: unknown; edited: boolean; stale: boolean; depends_on_facts?: boolean; fact_ids: string[]; evidence_ids: string[]; citation_ids: string[] }
export type Draft = { id: string; template_id: string; document_type: string; status: 'draft' | 'confirmed'; fields_json: Record<string, FieldState>; confirmed_at?: string; updated_at: string }

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : '请求失败')
    this.status = status
    this.detail = detail
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  const response = await fetch(`/api${path}`, { ...init, headers, credentials: 'include' })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new ApiError(response.status, body.detail)
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export const json = (value: unknown) => JSON.stringify(value)
