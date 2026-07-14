export type S3File = {
  key: string
  size: number
  last_modified: string
}

export type ListResponse = {
  bucket: string
  files: S3File[]
  folders: string[]
  count: number
}

// /ingest is async: the API classifies S3 objects vs the sync-service ledger
// and enqueues one RabbitMQ job per changed key. Workers drain the queue and
// update the ledger + Qdrant separately. Live progress arrives via SSE, not
// this response.
export type IngestResponse = {
  enqueued: number
  new: string[]
  modified: string[]
  deleted: string[]
  unchanged: string[]
  job_ids: string[]
}

const API_BASE = import.meta.env.VITE_API_BASE ?? ''

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}${body ? ` — ${body.slice(0, 300)}` : ''}`)
  }
  return (await res.json()) as T
}

export async function listS3Files(): Promise<ListResponse> {
  const res = await fetch(`${API_BASE}/s3/files`)
  return jsonOrThrow<ListResponse>(res)
}

export async function ingestAll(): Promise<IngestResponse> {
  const res = await fetch(`${API_BASE}/ingest`, { method: 'POST' })
  return jsonOrThrow<IngestResponse>(res)
}

export type UploadResponse = {
  uploaded: { key: string; bytes: number }[]
  failed: { key: string; error: string }[]
}

export async function uploadFiles(target: string, files: File[]): Promise<UploadResponse> {
  const form = new FormData()
  form.append('target', target)
  for (const f of files) form.append('files', f)
  const res = await fetch(`${API_BASE}/s3/upload`, { method: 'POST', body: form })
  return jsonOrThrow<UploadResponse>(res)
}

export async function createFolder(path: string): Promise<{ created: string }> {
  const res = await fetch(`${API_BASE}/s3/folder`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  })
  return jsonOrThrow<{ created: string }>(res)
}

export async function deleteFolder(path: string): Promise<{ deleted: string[]; count: number }> {
  const q = new URLSearchParams({ path }).toString()
  const res = await fetch(`${API_BASE}/s3/folder?${q}`, { method: 'DELETE' })
  return jsonOrThrow<{ deleted: string[]; count: number }>(res)
}

export async function deleteFile(key: string): Promise<{ deleted: string }> {
  const q = new URLSearchParams({ key }).toString()
  const res = await fetch(`${API_BASE}/s3/file?${q}`, { method: 'DELETE' })
  return jsonOrThrow<{ deleted: string }>(res)
}

// URL the browser can drop straight into an <img src>. Backed by GET /s3/object,
// which streams the object bytes with the sniffed content-type — no presigned URL
// dance, no S3 credentials leaked to the client.
export function s3ObjectUrl(key: string): string {
  return `${API_BASE}/s3/object?key=${encodeURIComponent(key)}`
}

// Current tail seq of the server-side event ring. Fetched before opening
// /events/stream so the initial connect only replays events after this seq,
// not the full history from prior ingest runs.
export async function eventsCursor(): Promise<number> {
  const res = await fetch(`${API_BASE}/events/cursor`)
  const body = await jsonOrThrow<{ seq: number }>(res)
  return body.seq
}

// Docling parse-preview types. The server runs Docling on an S3 object and
// returns the flattened element list without touching the pipeline (no
// captioning, no embedding, no Qdrant). Used by the "Preview parse" modal.
export type ParsePreviewTextElement = {
  kind: 'text'
  page: number
  text: string
}

export type ParsePreviewImageElement = {
  kind: 'image'
  page: number
  image_key: string
  caption_hint: string
  context_text: string
  img_index: number
  image_size: number
  // Omitted when the image exceeds max_image_bytes on the server.
  image_data_url?: string
}

export type ParsePreviewElement = ParsePreviewTextElement | ParsePreviewImageElement

export type ParsePreviewResponse = {
  doc_id: string
  version: string
  etag: string
  // 'hit' means the server returned a cached parse without hitting Docling;
  // 'miss' means Docling actually ran. Useful for verifying caching visually.
  cache: 'hit' | 'miss'
  stats: {
    elements: number
    text: number
    images: number
    pages: number[]
  }
  elements: ParsePreviewElement[]
}

export async function parsePreview(key: string): Promise<ParsePreviewResponse> {
  const res = await fetch(`${API_BASE}/parse-preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ key }),
  })
  return jsonOrThrow<ParsePreviewResponse>(res)
}

// Retrieval — hits the /retrieve endpoint which does hybrid search + rerank
// and returns raw chunks/images. No LLM augmentation (that lives in agent-service).
export type RetrievedChunk = {
  chunk_id: string
  text: string
  page: number
  kind: 'text' | 'image'
  score: number
}

export type RetrievedImage = {
  image_key: string
  url: string
  caption: string
  score: number
  // Short handle the LLM uses to embed the figure inline via [figure:HANDLE]
  // tokens in the generated answer. Present only on /generate responses.
  handle?: string
}

export type RetrieveTiming = {
  search_ms: number
  rerank_ms: number
  total_ms: number
  candidates: number
  chunks: number
  images: number
  device: string
  // Present only on /generate responses — how long the OpenAI synthesis took.
  generate_ms?: number
}

export type RetrieveResponse = {
  chunks: RetrievedChunk[]
  images: RetrievedImage[]
  timing?: RetrieveTiming
}

export type GenerateResponse = RetrieveResponse & {
  answer: string
}

export async function retrieveQuery(query: string, top_n = 8): Promise<RetrieveResponse> {
  const res = await fetch(`${API_BASE}/retrieve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_n }),
  })
  return jsonOrThrow<RetrieveResponse>(res)
}

// Same shape as /retrieve but the server also runs one OpenAI call to
// synthesize an `answer` string grounded in the retrieved chunks.
export async function generateQuery(query: string, top_n = 8): Promise<GenerateResponse> {
  const res = await fetch(`${API_BASE}/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, top_n }),
  })
  return jsonOrThrow<GenerateResponse>(res)
}

export type ResetResponse = {
  qdrant: string
  ledger_rows_removed: number
  artifacts_removed: number
}

export async function resetAll(): Promise<ResetResponse> {
  const res = await fetch(`${API_BASE}/reset`, { method: 'POST' })
  return jsonOrThrow<ResetResponse>(res)
}
