const API_ROOT = import.meta.env.VITE_API_ROOT ?? '/api'
const CSRF_COOKIE = 'arxivlens_csrf'

function readCookie(name: string) {
  const prefix = name + '='
  const item = document.cookie.split('; ').find((value) => value.startsWith(prefix))
  return item ? decodeURIComponent(item.slice(prefix.length)) : null
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const method = (options?.method ?? 'GET').toUpperCase()
  const headers = new Headers(options?.headers)
  if (options?.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const csrfToken = readCookie(CSRF_COOKIE)
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
  }
  const response = await fetch(API_ROOT + path, {
    ...options,
    credentials: 'include',
    headers,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail ?? body)
    } catch {
      detail = (await response.text()) || detail
    }
    if (response.status === 401 && path !== '/auth/login') {
      window.dispatchEvent(new Event('arxivlens:unauthorized'))
    }
    throw new ApiError(detail, response.status)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export async function streamApi(
  path: string,
  body: unknown,
  onEvent: (event: string, data: Record<string, unknown>) => void,
  signal?: AbortSignal,
) {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const csrfToken = readCookie(CSRF_COOKIE)
  if (csrfToken) headers.set('X-CSRF-Token', csrfToken)
  const response = await fetch(API_ROOT + path, {
    method: 'POST',
    credentials: 'include',
    headers,
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const payload = await response.json()
      detail = typeof payload.detail === 'string' ? payload.detail : JSON.stringify(payload.detail ?? payload)
    } catch {
      detail = (await response.text()) || detail
    }
    if (response.status === 401) window.dispatchEvent(new Event('arxivlens:unauthorized'))
    throw new ApiError(detail, response.status)
  }
  if (!response.body) throw new ApiError('浏览器未收到模型流式响应', 502)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      let event = 'message'
      const dataLines: string[] = []
      block.split('\n').forEach((line) => {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
      })
      if (dataLines.length) {
        try {
          onEvent(event, JSON.parse(dataLines.join('\n')) as Record<string, unknown>)
        } catch {
          throw new ApiError('模型流式响应格式错误', 502)
        }
      }
      boundary = buffer.indexOf('\n\n')
    }
    if (done) break
  }
}

export function toQuery(params: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') search.set(key, String(value))
  })
  const result = search.toString()
  return result ? '?' + result : ''
}

export function localDateString(date = new Date()) {
  const offsetDate = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return offsetDate.toISOString().slice(0, 10)
}

export function formatDateTime(value: string | null | undefined) {
  if (!value) return '-'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

export function formatDate(value: string | null | undefined) {
  if (!value) return '-'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(new Date(value))
}
