import { createSlice, nanoid } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'

export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

export type LogEntry = {
  id: string
  ts: string
  level: LogLevel
  logger: string
  message: string
  exception?: string
}

// Raw shape received from the /events/stream SSE endpoint.
export type ServerEvent =
  | { type: 'connected' }
  | {
      type: 'log'
      ts: string
      level: LogLevel
      logger: string
      message: string
      exception?: string
    }

export type LogFilter = 'info' | 'debug' | 'error'

export type EventsState = {
  streaming: boolean
  entries: LogEntry[]
  filter: LogFilter
}

// Cap in-store entries — 1k terminal-style rows renders fine without
// virtualization, and older entries scroll off the top naturally.
const MAX_ENTRIES = 1000

// Persist logs across page refreshes AND browser restarts in localStorage.
// Header dispatches clearEntries() before every /ingest, so what we persist
// is always the most recent run — the tab you open tomorrow shows the last
// ingest, not an accumulated history. `streaming` is intentionally NOT
// persisted: on load the EventSource is gone; if we rehydrated
// streaming=true we'd silently reopen a connection nobody asked for.
const STORAGE_KEY = 'ingest-ui.events.v1'

function loadPersisted(): { entries: LogEntry[]; filter: LogFilter } {
  const empty = { entries: [] as LogEntry[], filter: 'info' as LogFilter }
  if (typeof localStorage === 'undefined') return empty
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return empty
    const parsed = JSON.parse(raw)
    const entries = Array.isArray(parsed.entries)
      ? (parsed.entries as LogEntry[]).slice(-MAX_ENTRIES)
      : []
    const filter: LogFilter =
      parsed.filter === 'debug' || parsed.filter === 'error' ? parsed.filter : 'info'
    return { entries, filter }
  } catch {
    return empty
  }
}

const persisted = loadPersisted()

const initialState: EventsState = {
  streaming: false,
  entries: persisted.entries,
  // Default view: friendly summaries only. Non-technical users see plain-English
  // milestones; errors always break through regardless of this setting.
  filter: persisted.filter,
}

const eventsSlice = createSlice({
  name: 'events',
  initialState,
  reducers: {
    startStreaming(state) {
      state.streaming = true
    },
    stopStreaming(state) {
      state.streaming = false
    },
    clearEntries(state) {
      state.entries = []
    },
    setFilter(state, action: PayloadAction<LogFilter>) {
      state.filter = action.payload
    },
    entryReceived(state, action: PayloadAction<ServerEvent>) {
      const evt = action.payload
      if (evt.type !== 'log') return
      state.entries.push({
        id: nanoid(),
        ts: evt.ts,
        level: evt.level,
        logger: evt.logger,
        message: evt.message,
        exception: evt.exception,
      })
      if (state.entries.length > MAX_ENTRIES) {
        state.entries.splice(0, state.entries.length - MAX_ENTRIES)
      }
    },
  },
})

export const {
  startStreaming,
  stopStreaming,
  clearEntries,
  entryReceived,
  setFilter,
} = eventsSlice.actions
export default eventsSlice.reducer

/** Serialize the persisted slice fields to localStorage. Called by the
 * store subscription (throttled) — kept here so the STORAGE_KEY constant
 * has one owner. */
export function persistEvents(entries: LogEntry[], filter: LogFilter): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ entries, filter }))
  } catch {
    // quota exceeded — next update will retry; not fatal
  }
}

/**
 * Whether an entry should render given the active filter.
 *   - info  : `user` logger + all errors (non-tech view; failures always break through)
 *   - debug : everything
 *   - error : only ERROR / CRITICAL, regardless of logger
 */
export function entryMatchesFilter(entry: LogEntry, filter: LogFilter): boolean {
  const isError = entry.level === 'ERROR' || entry.level === 'CRITICAL'
  if (filter === 'debug') return true
  if (filter === 'error') return isError
  return entry.logger === 'user' || isError
}
