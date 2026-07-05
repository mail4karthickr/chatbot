export type Citation = {
  n: number
  chunk_id: string
  page: number
  kind: string
}

export type QueryImage = {
  image_key: string
  url: string
  caption?: string
}

export type ChatTurn = {
  role: 'user' | 'assistant'
  content: string
}

export type ToolCall = {
  tool: string
  input: unknown
  output?: string
}

export type ChatResponse = {
  answer: string
  citations: Citation[]
  images: QueryImage[]
  tool_calls?: ToolCall[]
}

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

export async function askAgent(message: string, history: ChatTurn[] = []): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, history }),
  })
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}${body ? ` — ${body.slice(0, 200)}` : ''}`)
  }
  return (await res.json()) as ChatResponse
}
