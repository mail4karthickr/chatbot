import { useEffect, useMemo, useRef } from 'react'
import { eventsCursor } from '../api'
import { useAppDispatch, useAppSelector } from '../app/hooks'
import {
  clearEntries,
  entryMatchesFilter,
  entryReceived,
  setFilter,
  stopStreaming,
} from '../features/events/eventsSlice'
import type { LogFilter, LogLevel, ServerEvent } from '../features/events/eventsSlice'
import { fetchS3Files } from '../features/s3/s3Slice'

/**
 * Live tail of the ingestion-service event stream.
 *
 * Mounts an `EventSource` only while `streaming` is true. On unmount / when
 * streaming flips off, the connection is closed. Browser EventSource will
 * auto-reconnect on transient errors.
 */
export function LogStream() {
  const dispatch = useAppDispatch()
  const streaming = useAppSelector((s) => s.events.streaming)
  const entries = useAppSelector((s) => s.events.entries)
  const filter = useAppSelector((s) => s.events.filter)
  const bodyRef = useRef<HTMLDivElement>(null)

  const visible = useMemo(
    () => entries.filter((e) => entryMatchesFilter(e, filter)),
    [entries, filter],
  )

  useEffect(() => {
    if (!streaming) return
    let es: EventSource | null = null
    let cancelled = false
    // Grab the server's current tail seq first so we only see events that
    // happen *after* this connect — otherwise re-clicking Ingest would dump
    // the ring buffer of the previous run into the fresh log view. If the
    // handshake fails (server down), fall back to since=0 and the server's
    // "cursor > current_seq → reset" branch takes care of it.
    eventsCursor()
      .catch(() => 0)
      .then((since) => {
        if (cancelled) return
        es = new EventSource(`/events/stream?since=${since}`)
        es.onmessage = (e) => {
          try {
            const evt = JSON.parse(e.data) as ServerEvent
            dispatch(entryReceived(evt))
            // When a worker job finishes (success or failure), the S3 tree is
            // stale — a successful ingest wrote artifacts under _artifacts/,
            // a failed one may have written a partial set. Refetch so the UI
            // reflects reality without the user having to hit Refresh.
            if (isJobTerminalEvent(evt)) {
              dispatch(fetchS3Files())
            }
          } catch {
            // ignore malformed frames — the server may emit non-JSON keepalives
          }
        }
      })
    return () => {
      cancelled = true
      if (es) es.close()
    }
  }, [streaming, dispatch])

  useEffect(() => {
    // Auto-scroll to bottom on new entries. Simple; doesn't try to respect
    // manual user scroll — good enough for a live tail.
    const el = bodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [visible])

  if (!streaming && entries.length === 0) return null

  const filters: { key: LogFilter; label: string; hint: string }[] = [
    { key: 'info', label: 'Info', hint: 'Friendly summaries for everyone' },
    { key: 'debug', label: 'Debug', hint: 'Full technical detail' },
    { key: 'error', label: 'Error', hint: 'Failures only' },
  ]

  return (
    <div className="log-stream">
      <div className="log-stream-header">
        <div className="log-stream-title">
          Live logs
          {streaming && <span className="live-dot" title="Streaming" />}
          <span className="log-count">
            {visible.length}
            {visible.length !== entries.length && ` / ${entries.length}`}
          </span>
        </div>
        <div className="log-stream-actions">
          <div className="filter-group" role="tablist">
            {filters.map((f) => (
              <button
                key={f.key}
                type="button"
                role="tab"
                aria-selected={filter === f.key}
                className={`filter-pill ${filter === f.key ? 'active' : ''}`}
                title={f.hint}
                onClick={() => dispatch(setFilter(f.key))}
              >
                {f.label}
              </button>
            ))}
          </div>
          <button
            className="btn secondary"
            onClick={() => dispatch(clearEntries())}
            disabled={entries.length === 0}
          >
            Clear
          </button>
          <button
            className="btn secondary"
            onClick={() => dispatch(stopStreaming())}
            disabled={!streaming}
          >
            Stop
          </button>
        </div>
      </div>
      <div className="log-stream-body" ref={bodyRef}>
        {visible.length === 0 ? (
          <div className="log-empty">
            {entries.length === 0
              ? 'Connected. Waiting for events…'
              : `No ${filter} entries yet. Switch to Debug to see everything.`}
          </div>
        ) : (
          visible.map((e) => (
            <div key={e.id} className="log-entry">
              <span className="log-ts">{formatTs(e.ts)}</span>
              <span className={`log-level ${levelClass(e.level)}`}>{e.level}</span>
              <span className="log-logger">{e.logger}</span>
              <span className="log-message">{e.message}</span>
              {e.exception && <pre className="log-exception">{e.exception}</pre>}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

/**
 * True for the worker's per-job completion markers: `job done ...` on success,
 * `job failed ...` on failure. Emitted exactly once per job, so this is safe
 * as a refresh trigger without debouncing (multi-file ingests just refresh
 * once per finished file — spaced by job duration).
 */
function isJobTerminalEvent(evt: ServerEvent): boolean {
  return (
    evt.type === 'log' &&
    evt.logger === 'worker' &&
    (evt.message.startsWith('job done ') || evt.message.startsWith('job failed '))
  )
}

function formatTs(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  const t = d.toLocaleTimeString(undefined, { hour12: false })
  const ms = String(d.getMilliseconds()).padStart(3, '0')
  return `${t}.${ms}`
}

function levelClass(level: LogLevel): string {
  switch (level) {
    case 'ERROR':
    case 'CRITICAL':
      return 'level-error'
    case 'WARNING':
      return 'level-warning'
    case 'DEBUG':
      return 'level-debug'
    default:
      return 'level-info'
  }
}
