import { createSlice, nanoid } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'
import type { RetrievedChunk, RetrievedImage } from '../../api'

export type ChatMessage = {
  id: string
  query: string
  status: 'loading' | 'success' | 'error'
  chunks?: RetrievedChunk[]
  images?: RetrievedImage[]
  error?: string
  // Client-measured round-trip in milliseconds — includes network + backend.
  // Populated on success and error, so users see how long a failed call took.
  durationMs?: number
}

export type ChatState = {
  messages: ChatMessage[]
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
}

const chatSlice = createSlice({
  name: 'chat',
  initialState,
  reducers: {
    queryStarted: {
      reducer(state, action: PayloadAction<{ id: string; query: string }>) {
        state.messages.push({
          id: action.payload.id,
          query: action.payload.query,
          status: 'loading',
        })
      },
      prepare(query: string) {
        return { payload: { id: nanoid(), query } }
      },
    },
    querySucceeded(
      state,
      action: PayloadAction<{
        id: string
        chunks: RetrievedChunk[]
        images: RetrievedImage[]
        durationMs: number
      }>,
    ) {
      const m = state.messages.find((x) => x.id === action.payload.id)
      if (!m) return
      m.status = 'success'
      m.chunks = action.payload.chunks
      m.images = action.payload.images
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
  },
})

export const { queryStarted, querySucceeded, queryFailed, clearChat } = chatSlice.actions
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
