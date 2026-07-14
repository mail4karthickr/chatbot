import type { ChatMessage } from '../features/chat/chatSlice'
import type { RetrievedImage } from '../api'

const FIGURE_TOKEN_RE = /\[figure:([A-Za-z0-9_-]+)\]/g

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

async function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = reject
    reader.readAsDataURL(blob)
  })
}

/** Fetch every unique image URL referenced by the transcript and cache it as a
 *  base64 data URL. Presigned URLs expire (~1h), so the export MUST inline the
 *  bytes at the moment of export — otherwise the HTML would render broken
 *  thumbnails when opened later or on another machine. Failed fetches drop
 *  from the map, and the HTML falls back to a "(image unavailable)" placeholder. */
async function fetchInlineImages(
  messages: ChatMessage[],
): Promise<Map<string, string>> {
  const byKey = new Map<string, string>()
  const jobs: Array<Promise<void>> = []
  const seen = new Set<string>()
  for (const m of messages) {
    for (const img of m.images ?? []) {
      if (seen.has(img.image_key)) continue
      seen.add(img.image_key)
      jobs.push(
        (async () => {
          try {
            const res = await fetch(img.url)
            if (!res.ok) return
            const blob = await res.blob()
            const dataUrl = await blobToDataUrl(blob)
            byKey.set(img.image_key, dataUrl)
          } catch {
            // network / expired presign / CORS — silently skip; renderer shows placeholder
          }
        })(),
      )
    }
  }
  await Promise.all(jobs)
  return byKey
}

function renderFigureCard(
  img: RetrievedImage | undefined,
  dataUrl: string | undefined,
  fallbackHandle: string,
): string {
  if (!img) {
    return `<span class="unknown-figure">[figure:${esc(fallbackHandle)}]</span>`
  }
  const alt = esc(img.caption || img.image_key)
  const caption = img.caption
    ? `<div class="fig-caption">${esc(img.caption)}</div>`
    : ''
  // Only embed the <img> when we successfully inlined the bytes as a data URL.
  // Never fall back to img.url — that's an S3 presigned URL containing the AWS
  // Access Key ID, which would leak into any shared or committed export.
  const imgOrWarning = dataUrl
    ? `<img src="${dataUrl}" alt="${alt}" />`
    : `<div class="fig-warning">(image unavailable — presigned URL likely expired; original key ${esc(img.image_key)})</div>`
  return `<figure class="inline-fig">
    ${imgOrWarning}
    ${caption}
  </figure>`
}

function renderAnswerBody(answer: string, images: RetrievedImage[], byKey: Map<string, string>): string {
  const byHandle = new Map<string, RetrievedImage>()
  for (const img of images) if (img.handle) byHandle.set(img.handle, img)

  let cursor = 0
  const out: string[] = []
  for (const match of answer.matchAll(FIGURE_TOKEN_RE)) {
    const start = match.index ?? 0
    if (start > cursor) {
      out.push(esc(answer.slice(cursor, start)))
    }
    const handle = match[1]
    const img = byHandle.get(handle)
    out.push(renderFigureCard(img, img ? byKey.get(img.image_key) : undefined, handle))
    cursor = start + match[0].length
  }
  if (cursor < answer.length) out.push(esc(answer.slice(cursor)))
  return out.join('')
}

function renderPassages(chunks: NonNullable<ChatMessage['chunks']>): string {
  return chunks
    .map(
      (c, i) => `
      <div class="passage">
        <div class="passage-meta">
          <span class="passage-rank">#${i + 1}</span>
          <span class="kind">${esc(c.kind)}</span>
          <span>page ${c.page}</span>
          <span class="passage-score">score ${c.score.toFixed(3)}</span>
        </div>
        <div class="passage-text">${esc(c.text)}</div>
      </div>
    `,
    )
    .join('')
}

function renderTurn(m: ChatMessage, byKey: Map<string, string>): string {
  const question = `<div class="q">${esc(m.query)}</div>`
  if (m.status === 'error') {
    return `<div class="turn">
      ${question}
      <div class="a error">Retrieval failed — ${esc(m.error || 'unknown error')}</div>
    </div>`
  }
  if (m.status !== 'success') return ''

  const nChunks = m.chunks?.length ?? 0
  const nImages = m.images?.length ?? 0
  const meta: string[] = []
  meta.push(`${nChunks} passage${nChunks === 1 ? '' : 's'}`)
  if (nImages > 0) meta.push(`${nImages} image${nImages === 1 ? '' : 's'}`)
  if (m.durationMs !== undefined) {
    meta.push(`${m.answer ? 'answered' : 'retrieved'} in ${formatDuration(m.durationMs)}`)
  }
  const metaLine = `<div class="a-meta">${meta.map(esc).join(' · ')}</div>`

  let answerBlock = ''
  if (m.answer) {
    answerBlock = `<div class="a">
      <div class="a-label">Answer</div>
      <div class="a-body">${renderAnswerBody(m.answer, m.images ?? [], byKey)}</div>
    </div>`
  } else if (nChunks === 0 && nImages === 0) {
    answerBlock = `<div class="a">
      <div class="a-label">No match</div>
      <div class="a-body">No passages or figures cleared the retrieval threshold.</div>
    </div>`
  } else {
    // Retrieval-only mode: the "answer" is the raw hits shown below.
    answerBlock = `<div class="a-note">Retrieval-only (generation was off) — inspect the passages below.</div>`
  }

  const passagesBlock =
    nChunks > 0
      ? `<details class="passages">
          <summary>Passages (${nChunks})</summary>
          ${renderPassages(m.chunks!)}
        </details>`
      : ''

  // In retrieval-only mode the raw image hits are informative on their own;
  // in generation mode the LLM already decided which figures to inline, so we
  // skip the redundant "other images" grid to keep the export tidy.
  const imagesBlock =
    !m.answer && nImages > 0
      ? `<div class="raw-images">
          <div class="raw-images-label">Images (${nImages})</div>
          <div class="raw-images-grid">
            ${(m.images ?? [])
              .map((img) => {
                const dataUrl = byKey.get(img.image_key)
                // Same rule as renderFigureCard: never emit img.url as fallback,
                // it embeds an AWS Access Key ID via the presigned URL.
                const imgTag = dataUrl
                  ? `<img src="${dataUrl}" alt="${esc(img.caption || img.image_key)}" />`
                  : ''
                return `<figure class="raw-image">
                  ${imgTag}
                  <figcaption>
                    <span class="passage-score">score ${img.score.toFixed(3)}</span>
                    ${img.caption ? `<span class="fig-caption">${esc(img.caption)}</span>` : ''}
                    ${!dataUrl ? `<span class="fig-warning">(image unavailable)</span>` : ''}
                  </figcaption>
                </figure>`
              })
              .join('')}
          </div>
        </div>`
      : ''

  return `<div class="turn">
    ${question}
    ${answerBlock}
    ${metaLine}
    ${passagesBlock}
    ${imagesBlock}
  </div>`
}

const STYLE = `
:root { color-scheme: light dark; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
       max-width: 900px; margin: 32px auto; padding: 0 24px; color: #1e1e21; background: #fafafa; line-height: 1.5; }
h1 { font-size: 22px; margin: 0 0 4px; }
.subtitle { color: #6b7280; font-size: 13px; margin-bottom: 24px; }
.turn { margin: 28px 0; padding-bottom: 20px; border-bottom: 1px solid #e2e2e5; }
.turn:last-child { border-bottom: none; }
.q { display: inline-block; max-width: 78%; margin-left: auto; float: right; clear: both;
     background: #4a7bd8; color: #fff; padding: 10px 14px; border-radius: 14px 14px 4px 14px;
     font-size: 14px; box-shadow: 0 1px 2px rgba(0,0,0,0.08); }
.a, .a-meta, .a-note, .passages, .raw-images { clear: both; }
.a { margin-top: 14px; padding: 14px 16px; border: 1px solid rgba(74,123,216,0.32);
     border-left-width: 3px; border-radius: 8px; background: rgba(74,123,216,0.06); }
.a.error { border-color: #ef4444; background: rgba(239,68,68,0.08); color: #ef4444; }
.a-label { font-size: 10.5px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase;
           color: #4a7bd8; margin-bottom: 6px; }
.a-body { font-size: 14px; white-space: pre-wrap; word-wrap: break-word; }
.a-meta { color: #6b7280; font-size: 12px; margin-top: 8px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.a-note { color: #6b7280; font-size: 13px; margin-top: 12px; font-style: italic; }
.inline-fig { margin: 12px 0; padding: 0; border: 1px solid #e2e2e5; border-radius: 8px; overflow: hidden; background: #fff; }
.inline-fig img { display: block; max-width: 100%; max-height: 320px; margin: 0 auto; object-fit: contain; background: #f2f3f5; }
.fig-caption { padding: 8px 12px; font-size: 12px; color: #6b7280; border-top: 1px solid #e2e2e5; }
.fig-warning { padding: 6px 12px; font-size: 11px; color: #b45309; background: rgba(245,158,11,0.08); border-top: 1px dashed #e2e2e5; }
.unknown-figure { display: inline-block; padding: 1px 6px; border-radius: 4px;
                  background: rgba(239,68,68,0.12); color: #ef4444;
                  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.passages { margin-top: 14px; }
.passages summary { cursor: pointer; font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
                    text-transform: uppercase; color: #6b7280; padding: 4px 0; user-select: none; }
.passage { margin: 10px 0; border: 1px solid #e2e2e5; border-radius: 8px; background: #fff; padding: 10px 12px; }
.passage-meta { display: flex; gap: 8px; align-items: center; font-size: 11.5px; color: #6b7280; margin-bottom: 6px; }
.passage-rank { font-weight: 700; color: #1e1e21; }
.kind { padding: 2px 8px; border-radius: 999px; background: rgba(127,127,127,0.15); font-weight: 600;
        text-transform: uppercase; letter-spacing: 0.04em; font-size: 10.5px; }
.passage-score { margin-left: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.passage-text { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px;
                white-space: pre-wrap; word-wrap: break-word; }
.raw-images { margin-top: 14px; }
.raw-images-label { font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
                    text-transform: uppercase; color: #6b7280; margin-bottom: 8px; }
.raw-images-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.raw-image { margin: 0; border: 1px solid #e2e2e5; border-radius: 8px; overflow: hidden; background: #fff; }
.raw-image img { display: block; width: 100%; max-height: 220px; object-fit: contain; background: #f2f3f5; }
.raw-image figcaption { padding: 8px 12px; display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: #6b7280; }
@media (prefers-color-scheme: dark) {
  body { background: #17171a; color: #e6e6e6; }
  .turn { border-color: #303035; }
  .a { background: rgba(74,123,216,0.10); }
  .a-label { color: #7fa3e6; }
  .inline-fig, .passage, .raw-image { background: #202024; border-color: #303035; }
  .fig-caption, .a-meta, .a-note, .subtitle, .passages summary, .raw-images-label, .passage-meta, .raw-image figcaption { color: #a0a0a8; }
  .passage-rank { color: #e6e6e6; }
  .inline-fig img, .raw-image img { background: #26262a; }
  .kind { background: #303035; }
}
`

function buildHtml(messages: ChatMessage[], byKey: Map<string, string>): string {
  const answered = messages.filter((m) => m.status === 'success' || m.status === 'error')
  const dateStr = new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC'
  const title = 'Knowledge base session'
  const body = answered.map((m) => renderTurn(m, byKey)).join('\n')
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>${esc(title)}</title>
<meta name="generator" content="ingestion-ui chat export" />
<style>${STYLE}</style>
</head>
<body>
<h1>${esc(title)}</h1>
<div class="subtitle">Exported ${esc(dateStr)} · ${answered.length} question${answered.length === 1 ? '' : 's'}</div>
${body}
</body>
</html>`
}

function safeFilename(): string {
  const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)
  return `chat-session-${ts}.html`
}

/** Fetch every referenced image, base64-inline it, build a self-contained HTML
 *  document with the full session (questions + answers + inline figures +
 *  collapsible passages), and trigger a browser download. */
export async function exportChatHtml(messages: ChatMessage[]): Promise<void> {
  const byKey = await fetchInlineImages(messages)
  const html = buildHtml(messages, byKey)
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = safeFilename()
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
