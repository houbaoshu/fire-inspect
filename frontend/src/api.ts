const API_ROOT = '/api/v1'

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message)
  }
}

function csrfToken(): string | undefined {
  return document.cookie
    .split('; ')
    .find((row) => row.startsWith('fire_csrf='))
    ?.split('=')[1]
}

export async function streamAssistant(
  payload: Record<string, unknown>,
  onToken: (text: string) => void,
): Promise<any> {
  const csrf = csrfToken()
  const response = await fetch(`${API_ROOT}/assistant/stream`, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}),
    },
    body: JSON.stringify(payload),
  })
  if (!response.ok || !response.body) throw new ApiError(response.status, '助手请求失败')
  const result: any = { content: '', citations: [], actions: [] }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const events = buffer.split('\n\n')
    buffer = events.pop() || ''
    for (const block of events) {
      const event = block.match(/^event: (.+)$/m)?.[1]
      const raw = block.match(/^data: (.+)$/m)?.[1]
      if (!event || !raw) continue
      const data = JSON.parse(raw)
      if (event === 'start') Object.assign(result, data)
      if (event === 'token') { result.content += data.text; onToken(data.text) }
      if (event === 'done') Object.assign(result, data)
    }
    if (done) break
  }
  return result
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (init.body && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  const csrf = csrfToken()
  if (csrf && !['GET', 'HEAD'].includes((init.method || 'GET').toUpperCase())) {
    headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  }
  const response = await fetch(`${API_ROOT}${path}`, { ...init, headers, credentials: 'include' })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: response.statusText }))
    throw new ApiError(response.status, payload.detail || '请求失败')
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function download(path: string, filename: string): Promise<void> {
  const csrf = csrfToken()
  const response = await fetch(`${API_ROOT}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {},
  })
  if (!response.ok) throw new ApiError(response.status, '导出失败')
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export async function uploadMaterial(
  inspectionId: string,
  file: File,
  onProgress: (progress: number) => void,
): Promise<unknown> {
  const session = await api<{ id: string; part_size: number; upload_mode: string }>(
    `/inspections/${inspectionId}/upload-sessions`,
    {
      method: 'POST',
      body: JSON.stringify({ filename: file.name, content_type: file.type || 'application/octet-stream', size_bytes: file.size }),
    },
  )
  const parts: Array<{ part_number: number; etag: string; size: number }> = []
  let number = 1
  for (let start = 0; start < file.size; start += session.part_size, number += 1) {
    const body = file.slice(start, Math.min(start + session.part_size, file.size))
    let part: { part_number: number; etag: string; size: number }
    if (session.upload_mode === 'presigned') {
      const signed = await api<{ url: string }>(`/upload-sessions/${session.id}/parts/${number}`)
      const response = await fetch(signed.url, { method: 'PUT', body })
      if (!response.ok) throw new ApiError(response.status, '文件分片上传失败')
      part = { part_number: number, etag: response.headers.get('etag')?.replaceAll('"', '') || '', size: body.size }
    } else {
      part = await api(`/upload-sessions/${session.id}/parts/${number}`, { method: 'PUT', body })
    }
    parts.push(part)
    onProgress(Math.round((Math.min(start + session.part_size, file.size) / file.size) * 100))
  }
  return api(`/upload-sessions/${session.id}/complete`, { method: 'POST', body: JSON.stringify({ parts }) })
}
