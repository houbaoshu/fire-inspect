import { ElMessage } from 'element-plus'

async function request(method: string, path: string, body?: any, form?: FormData) {
  const opt: RequestInit = { method, credentials: 'same-origin' }
  if (form) opt.body = form
  else if (body !== undefined) {
    opt.headers = { 'Content-Type': 'application/json' }
    opt.body = JSON.stringify(body)
  }
  const r = await fetch(path, opt)
  if (r.status === 401 && !path.includes('/auth/')) {
    location.href = '/login'
    throw new Error('未登录')
  }
  if (!r.ok) {
    let msg = `HTTP ${r.status}`
    try { msg = (await r.json()).detail || msg } catch {}
    ElMessage.error(msg)
    throw new Error(msg)
  }
  const ct = r.headers.get('content-type') || ''
  return ct.includes('json') ? r.json() : r.blob()
}

export const api = {
  get: (p: string) => request('GET', p),
  post: (p: string, body?: any, form?: FormData) => request('POST', p, body, form),
  patch: (p: string, body?: any) => request('PATCH', p, body),
  del: (p: string) => request('DELETE', p),
}

export function fmtMs(ms: number | null): string {
  if (ms == null) return ''
  const s = Math.floor(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
