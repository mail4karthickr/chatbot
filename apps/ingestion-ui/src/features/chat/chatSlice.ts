import { createSlice, nanoid } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'
import type { RetrievedChunk, RetrievedImage, RetrieveTiming } from '../../api'

export type ChatMessage = {
  id: string
  query: string
  status: 'loading' | 'success' | 'error'
  // ISO timestamp of when the query was fired. Used to associate streamed
  // user-log events (from the events slice) with this specific message.
  startedAt: string
  // Whether this query was fired with the Generation toggle on. Kept per-message
  // so a mixed history still renders correctly (some turns have answers, some don't).
  generated?: boolean
  // OpenAI-synthesized answer, only set when generated=true and the call succeeded.
  answer?: string
  chunks?: RetrievedChunk[]
  images?: RetrievedImage[]
  // Server-reported per-stage timing (search, rerank, total, device, counts).
  timing?: RetrieveTiming
  error?: string
  // Client-measured round-trip in milliseconds — includes network + backend.
  // Populated on success and error, so users see how long a failed call took.
  durationMs?: number
}

export type ChatState = {
  messages: ChatMessage[]
  // Toggle: when true, /ask hits POST /generate; when false, POST /retrieve.
  generateEnabled: boolean
}

const GENERATE_KEY = 'ingest-ui.chat.generate.v1'

function loadGenerateEnabled(): boolean {
  if (typeof localStorage === 'undefined') return false
  return localStorage.getItem(GENERATE_KEY) === '1'
}

// Persist chat history across refreshes and browser restarts. Cap the list so
// years of casual use don't blow the storage budget. Presigned image URLs may
// expire (default 1h from storage.presigned_url) — the thumbnail then breaks,
// but the text/score/query context is still useful; user can re-query for
// fresh URLs.
const STORAGE_KEY = 'ingest-ui.chat.v1'
const MAX_MESSAGES = 100

function loadPersisted(): ChatMessage[] {
  if (typeof localStorage === 'undefined') return []
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    // Drop any `loading` messages from a prior tab that never got to resolve.
    return (parsed as ChatMessage[])
      .filter((m) => m && m.id && m.query && m.status !== 'loading')
      .slice(-MAX_MESSAGES)
  } catch {
    return []
  }
}

const initialState: ChatState = {
  messages: loadPersisted(),
  generateEnabled: loadGenerateEnabled(),
}

const chatSlice = createSlice({
  name: 'chat',
  initialState,
  reducers: {
    queryStarted: {
      reducer(
        state,
        action: PayloadAction<{
          id: string
          query: string
          startedAt: string
          generated: boolean
        }>,
      ) {
        state.messages.push({
          id: action.payload.id,
          query: action.payload.query,
          status: 'loading',
          startedAt: action.payload.startedAt,
          generated: action.payload.generated,
        })
      },
      prepare(query: string, generated: boolean) {
        return {
          payload: {
            id: nanoid(),
            query,
            startedAt: new Date().toISOString(),
            generated,
          },
        }
      },
    },
    querySucceeded(
      state,
      action: PayloadAction<{
        id: string
        answer?: string
        chunks: RetrievedChunk[]
        images: RetrievedImage[]
        timing?: RetrieveTiming
        durationMs: number
      }>,
    ) {
      const m = state.messages.find((x) => x.id === action.payload.id)
      if (!m) return
      m.status = 'success'
      m.answer = action.payload.answer
      m.chunks = action.payload.chunks
      m.images = action.payload.images
      m.timing = action.payload.timing
      m.durationMs = action.payload.durationMs
    },
    queryFailed(
      state,
      action: PayloadAction<{ id: string; error: string; durationMs: number }>,
    ) {
      const m = state.messages.find((x) => x.id === action.payload.id)
      if (!m) return
      m.status = 'error'
      m.error = action.payload.error
      m.durationMs = action.payload.durationMs
    },
    clearChat(state) {
      state.messages = []
    },
    setGenerateEnabled(state, action: PayloadAction<boolean>) {
      state.generateEnabled = action.payload
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem(GENERATE_KEY, action.payload ? '1' : '0')
      }
    },
  },
})

export const {
  queryStarted,
  querySucceeded,
  queryFailed,
  clearChat,
  setGenerateEnabled,
} = chatSlice.actions
export default chatSlice.reducer

/** Serialize chat messages to localStorage. Called by the store subscription. */
export function persistChat(messages: ChatMessage[]): void {
  if (typeof localStorage === 'undefined') return
  try {
    // Cap on write too, in case the in-memory array grew past MAX_MESSAGES.
    localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-MAX_MESSAGES)))
  } catch {
    // quota exceeded — next update will retry; not fatal
  }
}
