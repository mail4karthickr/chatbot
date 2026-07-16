import { useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { generateQuery, retrieveQuery } from '../api'
import type { RetrievedChunk, RetrievedImage } from '../api'
import { exportChatHtml } from '../utils/exportChatHtml'
import { ChevronIcon, DownloadIcon } from './icons'
import { useAppDispatch, useAppSelector } from '../app/hooks'
import {
  clearChat,
  queryFailed,
  queryStarted,
  querySucceeded,
  setGenerateEnabled,
} from '../features/chat/chatSlice'
import type { ChatMessage } from '../features/chat/chatSlice'
import { startStreaming } from '../features/events/eventsSlice'
import type { LogEntry } from '../features/events/eventsSlice'

/**
 * Query card. Hits POST /retrieve for raw reranked chunks + image hits, or
 * POST /generate for an OpenAI-synthesized answer grounded in those chunks —
 * toggled by the "Generation" switch in the header.
 */
export function ChatCard() {
  const dispatch = useAppDispatch()
  const messages = useAppSelector((s) => s.chat.messages)
  const generateEnabled = useAppSelector((s) => s.chat.generateEnabled)
  const events = useAppSelector((s) => s.events.entries)
  const [input, setInput] = useState('')
  const [exporting, setExporting] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  const busy = messages.some((m) => m.status === 'loading')
  const exportable = messages.some(
    (m) => m.status === 'success' || m.status === 'error',
  )

  async function onExport() {
    if (exporting) return
    setExporting(true)
    try {
      await exportChatHtml(messages)
    } finally {
      setExporting(false)
    }
  }

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const q = input.trim()
    if (!q || busy) return
    setInput('')

    // Ensure the SSE connection is open so the ChatCard sees per-stage
    // retrieval events live. No-op if already streaming.
    dispatch(startStreaming())
    // Snapshot the toggle at submit time — flipping it while a query is in
    // flight shouldn't retroactively change what this message expects back.
    const useGen = generateEnabled
    const action = dispatch(queryStarted(q, useGen))
    const id = action.payload.id
    const t0 = performance.now()
    try {
      const res = useGen ? await generateQuery(q, 8) : await retrieveQuery(q, 8)
      dispatch(
        querySucceeded({
          id,
          answer: 'answer' in res ? res.answer : undefined,
          chunks: res.chunks,
          images: res.images,
          timing: res.timing,
          durationMs: performance.now() - t0,
        }),
      )
    } catch (err) {
      dispatch(
        queryFailed({
          id,
          error: err instanceof Error ? err.message : 'Query failed',
          durationMs: performance.now() - t0,
        }),
      )
    }
  }

  return (
    <div className="chat-card">
      <div className="chat-header">
        <div className="chat-title">Ask the knowledge base</div>
        <div className="chat-header-actions">
          <button
            type="button"
            role="switch"
            aria-checked={generateEnabled}
            className={`chat-toggle${generateEnabled ? ' on' : ''}`}
            onClick={() => dispatch(setGenerateEnabled(!generateEnabled))}
            title={
              generateEnabled
                ? 'Generation on — an LLM answer will be synthesized from the retrieved passages'
                : 'Generation off — only raw retrieved passages will be shown'
            }
          >
            <span className="chat-toggle-track"><span className="chat-toggle-thumb" /></span>
            <span className="chat-toggle-label">
              Generation {generateEnabled ? 'on' : 'off'}
            </span>
          </button>
          <button
            type="button"
            className="chat-export-btn"
            onClick={onExport}
            disabled={!exportable || exporting}
            title="Download the entire session as a self-contained HTML — questions, answers, inline figures, and expandable passages. Great for feeding to another model for validation."
          >
            <DownloadIcon />
            <span>{exporting ? 'Exporting…' : 'Export HTML'}</span>
          </button>
          <button
            type="button"
            className="btn secondary"
            onClick={() => dispatch(clearChat())}
            disabled={messages.length === 0}
          >
            Clear
          </button>
        </div>
      </div>

      <div className="chat-body" ref={scrollRef}>
        {messages.length === 0 ? (
          <div className="chat-empty">
            Ask a question about your ingested documents. You'll get back the
            top-ranked passages and any relevant figures. Flip{' '}
            <strong>Generation</strong> on to also get a synthesized answer
            grounded in those passages.
          </div>
        ) : (
          messages.map((m) => <ChatTurn key={m.id} m={m} events={events} />)
        )}
      </div>

      <form className="chat-input" onSubmit={submit}>
        <input
          className="chat-input-field"
          type="text"
          value={input}
          placeholder="e.g. what does the study conclude about outcomes?"
          onChange={(e) => setInput(e.target.value)}
          disabled={busy}
          autoFocus
        />
        <button type="submit" className="btn primary" disabled={busy || !input.trim()}>
          {busy ? 'Searching…' : 'Ask'}
        </button>
      </form>
    </div>
  )
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(2)} s`
}

function ChatTurn({ m, events }: { m: ChatMessage; events: LogEntry[] }) {
  // Live per-stage progress lines. Any `user`-logger event newer than the
  // message's startedAt is treated as belonging to this query. ISO timestamps
  // compare lexicographically, so no Date parsing is needed on the hot path.
  const progress = useMemo(() => {
    if (m.status !== 'loading' || !m.startedAt) return [] as LogEntry[]
    return events.filter((e) => e.logger === 'user' && e.ts >= m.startedAt)
  }, [events, m.status, m.startedAt])

  return (
    <div className="chat-turn">
      <div className="chat-bubble user">{m.query}</div>

      {m.status === 'loading' && (
        <div className="chat-response loading">
          <div>Searching…</div>
          {progress.length > 0 && (
            <ul className="chat-progress">
              {progress.map((e) => (
                <li key={e.id}>{e.message}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {m.status === 'error' && (
        <div className="chat-response error">
          Retrieval failed — {m.error}
          {m.durationMs !== undefined && (
            <span className="chat-duration"> · {formatDuration(m.durationMs)}</span>
          )}
        </div>
      )}

      {m.status === 'success' && (
        <div className="chat-response">
          {m.answer && (
            <div className="chat-answer">
              <div className="chat-answer-label">Answer</div>
              <AnswerBody answer={m.answer} images={m.images ?? []} />
            </div>
          )}
          <div className="chat-response-meta">
            {(m.chunks?.length ?? 0)} passage{(m.chunks?.length ?? 0) === 1 ? '' : 's'}
            {(m.images?.length ?? 0) > 0 && `, ${m.images!.length} image${m.images!.length === 1 ? '' : 's'}`}
            {m.durationMs !== undefined && (
              <span className="chat-duration">
                {' · '}
                {m.answer ? 'answered' : 'retrieved'} in {formatDuration(m.durationMs)}
              </span>
            )}
          </div>
          {m.timing && (
            <div className="chat-timing">
              <span>vector search {formatDuration(m.timing.search_ms)}</span>
              <span>·</span>
              <span>rerank {formatDuration(m.timing.rerank_ms)}</span>
              {m.timing.generate_ms !== undefined && (
                <>
                  <span>·</span>
                  <span>generate {formatDuration(m.timing.generate_ms)}</span>
                </>
              )}
              <span>·</span>
              <span>{m.timing.candidates} candidates</span>
              <span>·</span>
              <span>{m.timing.device}</span>
            </div>
          )}
          {(m.chunks?.length ?? 0) === 0 && (m.images?.length ?? 0) === 0 ? (
            <div className="chat-empty-result">
              No matches. Try different wording, or check that ingestion has
              completed.
            </div>
          ) : (
            <>
              {m.chunks && m.chunks.length > 0 && (
                <ChunksSection chunks={m.chunks} />
              )}

              {/* In retrieval-only mode there is no LLM to inline figures, so
                  the raw image hits are surfaced here as their own section.
                  When generation is on, the LLM already decided which figures
                  materially support the answer (and embedded them inline via
                  [figure:HANDLE]); the passages section carries the caption
                  text for anything it chose to skip, so a duplicate "Other
                  retrieved images" list would just be noise. */}
              {!m.answer && m.images && m.images.length > 0 && (
                <div className="chat-section">
                  <div className="chat-section-label">
                    Images ({m.images.length})
                  </div>
                  <div className="chat-images">
                    {m.images.map((img) => (
                      <a
                        key={img.image_key}
                        className="image-card"
                        href={img.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        <img src={img.url} alt={img.caption || img.image_key} />
                        <div className="image-meta">
                          <span className="chunk-score">
                            score {img.score.toFixed(3)}
                          </span>
                          {img.caption && (
                            <span className="image-caption">{img.caption}</span>
                          )}
                        </div>
                      </a>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** Collapsed by default — chunks are diagnostic detail; the answer (or empty-
 *  result banner) is the primary content. Expand-on-click via the header. */
function ChunksSection({ chunks }: { chunks: RetrievedChunk[] }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="chat-section">
      <button
        type="button"
        className="chat-section-toggle"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <ChevronIcon open={open} />
        <span>Passages ({chunks.length})</span>
        <span className="chat-section-hint">
          {open ? 'click to hide' : 'click to inspect'}
        </span>
      </button>
      {open &&
        chunks.map((c, i) => (
          <div key={c.chunk_id} className="chunk-card">
            <div className="chunk-meta">
              <span className="chunk-rank">#{i + 1}</span>
              <span className={`chunk-kind kind-${c.kind}`}>{c.kind}</span>
              <span className="chunk-page">page {c.page}</span>
              <span className="chunk-score">score {c.score.toFixed(3)}</span>
            </div>
            <div className="chunk-text">{c.text}</div>
          </div>
        ))}
    </div>
  )
}

// Matches `[figure:HANDLE]` tokens the LLM inserts to embed a figure at a
// specific spot in the answer. Handle chars are kept liberal so future
// server-side formats (e.g. "f1", "fig-3", "12") continue to render.
const FIGURE_TOKEN_RE = /\[figure:([A-Za-z0-9_-]+)\]/g

/** Render the answer text, replacing every `[figure:HANDLE]` token with an
 *  inline figure card. Unknown handles fall back to the raw token so the
 *  problem is visible instead of silently swallowed. */
function AnswerBody({
  answer,
  images,
}: {
  answer: string
  images: RetrievedImage[]
}) {
  const byHandle = useMemo(() => {
    const map = new Map<string, RetrievedImage>()
    for (const img of images) {
      if (img.handle) map.set(img.handle, img)
    }
    return map
  }, [images])

  const parts = useMemo(() => splitByFigureTokens(answer), [answer])

  return (
    <div className="chat-answer-text">
      {parts.map((part, i) =>
        part.type === 'text' ? (
          <ReactMarkdown key={i}>{part.value}</ReactMarkdown>
        ) : byHandle.has(part.handle) ? (
          <InlineFigure key={i} image={byHandle.get(part.handle)!} />
        ) : (
          <span key={i} className="chat-answer-unknown-figure">
            {part.raw}
          </span>
        ),
      )}
    </div>
  )
}

type AnswerPart =
  | { type: 'text'; value: string }
  | { type: 'figure'; handle: string; raw: string }

function splitByFigureTokens(answer: string): AnswerPart[] {
  const parts: AnswerPart[] = []
  let cursor = 0
  for (const match of answer.matchAll(FIGURE_TOKEN_RE)) {
    const start = match.index ?? 0
    if (start > cursor) {
      parts.push({ type: 'text', value: answer.slice(cursor, start) })
    }
    parts.push({ type: 'figure', handle: match[1], raw: match[0] })
    cursor = start + match[0].length
  }
  if (cursor < answer.length) {
    parts.push({ type: 'text', value: answer.slice(cursor) })
  }
  return parts
}

function InlineFigure({ image }: { image: RetrievedImage }) {
  return (
    <a
      className="inline-figure"
      href={image.url}
      target="_blank"
      rel="noreferrer"
    >
      <img src={image.url} alt={image.caption || image.image_key} />
      {image.caption && (
        <div className="inline-figure-caption">{image.caption}</div>
      )}
    </a>
  )
}
