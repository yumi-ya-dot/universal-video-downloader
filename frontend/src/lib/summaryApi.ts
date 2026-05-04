import { apiUrl } from './apiBase'

export type SummaryMindMapNode = {
  title: string
  children: SummaryMindMapNode[]
}

export type SummaryPayload = {
  title: string
  overview: string
  key_points: string[]
  chapter_outline: string[]
  mind_map_mermaid: string
  mind_map_tree?: SummaryMindMapNode
}

export type SubtitleSegment = {
  start: number
  end: number
  text: string
}

export type SubtitleTrack = {
  language: string
  language_label: string | null
  source: 'subtitle' | 'automatic_caption' | 'danmaku' | 'metadata'
  segments: SubtitleSegment[]
  full_text: string
}

export type ChatAnswer = {
  answer: string
}

export type SummaryDelta = {
  delta: string
}

export async function postJson<T>(url: string, payload: unknown): Promise<T> {
  const response = await fetch(apiUrl(url), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const data = await response.json()
  if (!response.ok) throw new Error(data.detail || '请求失败')
  return data as T
}

export async function readSSE<T extends object>(url: string, payload: unknown, handlers: {
  onEvent: (event: string, data: T | Record<string, unknown>) => void
}, options?: { signal?: AbortSignal }) {
  const response = await fetch(apiUrl(url), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(payload),
    signal: options?.signal,
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error((data as { detail?: string }).detail || '流式请求失败')
  }

  const reader = response.body?.getReader()
  if (!reader) throw new Error('浏览器不支持流式读取')

  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() || ''

    for (const chunk of chunks) {
      const lines = chunk.split('\n')
      const eventLine = lines.find((line) => line.startsWith('event:'))
      const dataLine = lines.find((line) => line.startsWith('data:'))
      if (!eventLine || !dataLine) continue
      const event = eventLine.slice(6).trim()
      const jsonText = dataLine.slice(5).trim()
      handlers.onEvent(event, JSON.parse(jsonText))
    }
  }
}
