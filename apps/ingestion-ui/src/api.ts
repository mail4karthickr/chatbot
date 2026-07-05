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

export type Downloaded = {
  key: string
  path: string
  bytes: number
}

export type FailedDownload = {
  key: string
  error: string
}

export type IngestResponse = {
  downloaded: Downloaded[]
  failed: FailedDownload[]
  total_bytes: number
  dest_dir: string
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

export type ResetResponse = {
  qdrant: string
  ledger_rows_removed: number
}

export async function resetAll(): Promise<ResetResponse> {
  const res = await fetch(`${API_BASE}/reset`, { method: 'POST' })
  return jsonOrThrow<ResetResponse>(res)
}
