import { useEffect, useState } from 'react'
import { parsePreview } from '../../api'
import type {
  ParsePreviewElement,
  ParsePreviewImageElement,
  ParsePreviewResponse,
} from '../../api'
import { formatBytes } from '../../utils/format'
import { DownloadIcon } from '../icons'

export function ParsePreview({
  fileKey,
  bucket,
}: {
  fileKey: string
  bucket: string
}) {
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [data, setData] = useState<ParsePreviewResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setStatus('loading')
    setError(null)
    parsePreview(fileKey)
      .then((res) => {
        if (cancelled) return
        setData(res)
        setStatus('ready')
      })
      .catch((err) => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'Parse failed')
        setStatus('error')
      })
    return () => {
      cancelled = true
    }
  }, [fileKey])

  return (
    <div className="parse-preview">
      <div className="path-hint">
        <code>s3://{bucket}/{fileKey}</code>
      </div>

      {status === 'loading' && (
        <div className="parse-status">
          Parsing with Docling… (this reads layout, tables, and figures — usually
          10–60s the first time, cached afterwards)
        </div>
      )}

      {status === 'error' && (
        <div className="parse-status error">Parse failed — {error}</div>
      )}

      {status === 'ready' && data && (
        <>
          <div className="parse-header-row">
            <div className="parse-stats">
              <StatBadge label="elements" value={data.stats.elements} />
              <StatBadge label="text" value={data.stats.text} />
              <StatBadge label="images" value={data.stats.images} />
              <StatBadge
                label="pages"
                value={
                  data.stats.pages.length > 0
                    ? `${data.stats.pages[0]}–${data.stats.pages[data.stats.pages.length - 1]}`
                    : '—'
                }
              />
              <StatBadge label="version" value={data.version.slice(0, 8)} mono />
            </div>
            <button
              type="button"
              className="parse-export-btn"
              onClick={() => downloadHtml(data, fileKey)}
              title="Download a self-contained HTML dump — text sections + inline image thumbnails, no external assets"
            >
              <DownloadIcon />
              <span>Export HTML</span>
            </button>
          </div>

          <div className="parse-elements">
            {data.elements.map((el, i) => (
              <ElementCard key={i} el={el} index={i} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function esc(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function buildExportHtml(data: ParsePreviewResponse, fileKey: string): string {
  const pageRange =
    data.stats.pages.length > 0
      ? `${data.stats.pages[0]}–${data.stats.pages[data.stats.pages.length - 1]}`
      : '—'
  const title = `Docling parse — ${fileKey}`
  const style = `
    :root { color-scheme: light dark; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
           max-width: 1100px; margin: 32px auto; padding: 0 24px; color: #1e1e21; background: #fafafa; }
    h1 { font-size: 22px; margin: 0 0 4px; }
    .subtitle { color: #6b7280; font-size: 13px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
             gap: 8px; margin: 20px 0; }
    .stat { border: 1px solid #e2e2e5; border-radius: 8px; padding: 8px 10px; background: #fff; }
    .stat-label { font-size: 10.5px; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase; color: #6b7280; }
    .stat-value { font-size: 15px; font-weight: 600; }
    .stat-value.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; }
    .el { border: 1px solid #e2e2e5; border-radius: 8px; background: #fff; padding: 10px 12px; margin: 10px 0; }
    .el.kind-image { border-left: 3px solid rgba(74, 123, 216, 0.4); }
    .el-meta { display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: #6b7280; margin-bottom: 6px; }
    .el-rank { font-weight: 700; color: #1e1e21; }
    .kind { padding: 2px 8px; border-radius: 999px; background: rgba(127, 127, 127, 0.15); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; font-size: 10.5px; }
    .el-size { margin-left: auto; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
    .el-text { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; line-height: 1.55;
               white-space: pre-wrap; word-wrap: break-word; }
    .el-image { display: grid; grid-template-columns: minmax(180px, 240px) 1fr; gap: 14px; align-items: start; }
    .el-image-frame { display: flex; align-items: center; justify-content: center; min-height: 120px;
                      background: #f2f3f5; border: 1px solid #e2e2e5; border-radius: 6px; overflow: hidden; }
    .el-image-frame img { max-width: 100%; max-height: 240px; object-fit: contain; }
    .el-side { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
    .el-line-label { font-size: 10.5px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: #6b7280; }
    .el-line-value { font-size: 12.5px; word-wrap: break-word; }
    .el-line-value.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
    .el-line-value.wrap { white-space: pre-wrap; }
    .placeholder { color: #6b7280; font-size: 12px; text-align: center; padding: 12px; }
    @media (prefers-color-scheme: dark) {
      body { background: #17171a; color: #e6e6e6; }
      .stat, .el, .el-image-frame { background: #202024; border-color: #303035; }
      .kind, .el-image-frame { background: #26262a; }
      .stat-label, .el-meta, .el-line-label, .placeholder, .subtitle { color: #a0a0a8; }
      .el-rank { color: #e6e6e6; }
    }
  `.trim()

  const elementsHtml = data.elements
    .map((el, i) => {
      const meta = `
        <div class="el-meta">
          <span class="el-rank">#${i + 1}</span>
          <span class="kind">${esc(el.kind)}</span>
          <span>page ${el.page}</span>
          ${
            el.kind === 'image'
              ? `<span class="el-size">${formatBytes(el.image_size)}</span>`
              : ''
          }
        </div>`
      if (el.kind === 'text') {
        return `<div class="el kind-text">${meta}<div class="el-text">${esc(el.text)}</div></div>`
      }
      const imgBlock = el.image_data_url
        ? `<img src="${el.image_data_url}" alt="${esc(el.caption_hint || el.image_key)}" />`
        : `<div class="placeholder">Image too large to inline (${formatBytes(el.image_size)}). Stored at ${esc(el.image_key)} during ingestion.</div>`
      return `<div class="el kind-image">${meta}
        <div class="el-image">
          <div class="el-image-frame">${imgBlock}</div>
          <div class="el-side">
            <div><div class="el-line-label">image_key</div><div class="el-line-value mono">${esc(el.image_key)}</div></div>
            <div><div class="el-line-label">caption_hint (Docling)</div><div class="el-line-value wrap">${esc(el.caption_hint || '—')}</div></div>
            <div><div class="el-line-label">context_text (reading-order neighbours)</div><div class="el-line-value wrap">${esc(el.context_text || '—')}</div></div>
          </div>
        </div>
      </div>`
    })
    .join('\n')

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>${esc(title)}</title>
<meta name="generator" content="ingestion-ui parse-preview export" />
<style>${style}</style>
</head>
<body>
<h1>${esc(title)}</h1>
<div class="subtitle">s3://${esc(fileKey)} · version ${esc(data.version)} · etag ${esc(data.etag)}</div>
<div class="stats">
  <div class="stat"><div class="stat-label">elements</div><div class="stat-value">${data.stats.elements}</div></div>
  <div class="stat"><div class="stat-label">text</div><div class="stat-value">${data.stats.text}</div></div>
  <div class="stat"><div class="stat-label">images</div><div class="stat-value">${data.stats.images}</div></div>
  <div class="stat"><div class="stat-label">pages</div><div class="stat-value">${esc(pageRange)}</div></div>
  <div class="stat"><div class="stat-label">version</div><div class="stat-value mono">${esc(data.version.slice(0, 8))}</div></div>
</div>
${elementsHtml}
</body>
</html>`
}

function safeFilename(fileKey: string): string {
  // "docs/InsuranceMother.pdf" → "InsuranceMother.parse.html"
  const base = fileKey.split('/').pop() || 'document'
  const stem = base.replace(/\.[^.]+$/, '') || base
  return `${stem}.parse.html`
}

function downloadHtml(data: ParsePreviewResponse, fileKey: string): void {
  const html = buildExportHtml(data, fileKey)
  const blob = new Blob([html], { type: 'text/html;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = safeFilename(fileKey)
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function StatBadge({
  label,
  value,
  mono,
}: {
  label: string
  value: string | number
  mono?: boolean
}) {
  return (
    <div className="parse-stat">
      <div className="parse-stat-label">{label}</div>
      <div className={`parse-stat-value${mono ? ' mono' : ''}`}>{value}</div>
    </div>
  )
}

function ElementCard({ el, index }: { el: ParsePreviewElement; index: number }) {
  return (
    <div className={`parse-el kind-${el.kind}`}>
      <div className="parse-el-meta">
        <span className="parse-el-rank">#{index + 1}</span>
        <span className={`chunk-kind kind-${el.kind}`}>{el.kind}</span>
        <span className="parse-el-page">page {el.page}</span>
        {el.kind === 'image' && (
          <span className="parse-el-size mono">
            {formatBytes(el.image_size)}
          </span>
        )}
      </div>
      {el.kind === 'text' ? (
        <div className="parse-el-text">{el.text}</div>
      ) : (
        <ImageElementBody el={el} />
      )}
    </div>
  )
}

function ImageElementBody({ el }: { el: ParsePreviewImageElement }) {
  return (
    <div className="parse-el-image">
      <div className="parse-el-image-frame">
        {el.image_data_url ? (
          <img src={el.image_data_url} alt={el.caption_hint || el.image_key} />
        ) : (
          <div className="parse-status">
            Image too large to inline ({formatBytes(el.image_size)}) — will be
            uploaded to S3 during ingestion.
          </div>
        )}
      </div>
      <div className="parse-el-image-side">
        <LabeledLine label="image_key" value={el.image_key} mono />
        <LabeledLine
          label="caption_hint (Docling)"
          value={el.caption_hint || '—'}
        />
        <LabeledLine
          label="context_text (reading-order neighbours)"
          value={el.context_text || '—'}
          wrap
        />
      </div>
    </div>
  )
}

function LabeledLine({
  label,
  value,
  mono,
  wrap,
}: {
  label: string
  value: string
  mono?: boolean
  wrap?: boolean
}) {
  return (
    <div className="parse-el-line">
      <div className="parse-el-line-label">{label}</div>
      <div
        className={`parse-el-line-value${mono ? ' mono' : ''}${wrap ? ' wrap' : ''}`}
      >
        {value}
      </div>
    </div>
  )
}
