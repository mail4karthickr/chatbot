import { useEffect, useRef, useState } from 'react'
import { retrieveQuery } from '../api'
import { useAppDispatch, useAppSelector } from '../app/hooks'
import {
  clearChat,
  queryFailed,
  queryStarted,
  querySucceeded,
} from '../features/chat/chatSlice'
import type { ChatMessage } from '../features/chat/chatSlice'

/**
 * Retrieval-only query card. Hits POST /retrieve and displays the raw
 * reranked chunks + image hits. No LLM augmentation — this is for
 * verifying that ingested content is actually retrievable.
 */
export function ChatCard() {
  const dispatch = useAppDispatch()
  const messages = useAppSelector((s) => s.chat.messages)
  const [input, setInput] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)

  const busy = messages.some((m) => m.status === 'loading')

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const q = input.trim()
    if (!q || busy) return
    setInput('')

    const action = dispatch(queryStarted(q))
    const id = action.payload.id
    const t0 = performance.now()
    try {
      const res = await retrieveQuery(q, 8)
      dispatch(
        querySucceeded({
          id,
          chunks: res.chunks,
          images: res.images,
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
          <span className="chat-badge">Retrieval only — no LLM</span>
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
            top-ranked passages and any relevant figures — the raw retrieval
            output, no answer generation.
          </div>
        ) : (
          messages.map((m) => <ChatTurn key={m.id} m={m} />)
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

function ChatTurn({ m }: { m: ChatMessage }) {
  return (
    <div className="chat-turn">
      <div className="chat-bubble user">{m.query}</div>

      {m.status === 'loading' && (
        <div className="chat-response loading">Searching…</div>
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
          <div className="chat-response-meta">
            {(m.chunks?.length ?? 0)} passage{(m.chunks?.length ?? 0) === 1 ? '' : 's'}
            {(m.images?.length ?? 0) > 0 && `, ${m.images!.length} image${m.images!.length === 1 ? '' : 's'}`}
            {m.durationMs !== undefined && (
              <span className="chat-duration"> · retrieved in {formatDuration(m.durationMs)}</span>
            )}
          </div>
          {(m.chunks?.length ?? 0) === 0 && (m.images?.length ?? 0) === 0 ? (
            <div className="chat-empty-result">
              No matches. Try different wording, or check that ingestion has
              completed.
            </div>
          ) : (
            <>
              {m.chunks && m.chunks.length > 0 && (
                <div className="chat-section">
                  <div className="chat-section-label">
                    Passages ({m.chunks.length})
                  </div>
                  {m.chunks.map((c, i) => (
                    <div key={c.chunk_id} className="chunk-card">
                      <div className="chunk-meta">
                        <span className="chunk-rank">#{i + 1}</span>
                        <span className={`chunk-kind kind-${c.kind}`}>{c.kind}</span>
                        <span className="chunk-page">page {c.page}</span>
                        <span className="chunk-score">
                          score {c.score.toFixed(3)}
                        </span>
                      </div>
                      <div className="chunk-text">{c.text}</div>
                    </div>
                  ))}
                </div>
              )}

              {m.images && m.images.length > 0 && (
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
