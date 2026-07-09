import { configureStore } from '@reduxjs/toolkit'
import s3Reducer from '../features/s3/s3Slice'
import ingestReducer from '../features/ingest/ingestSlice'
import uiReducer from '../features/ui/uiSlice'
import eventsReducer, { persistEvents } from '../features/events/eventsSlice'
import chatReducer, { persistChat } from '../features/chat/chatSlice'

export const store = configureStore({
  reducer: {
    s3: s3Reducer,
    ingest: ingestReducer,
    ui: uiReducer,
    events: eventsReducer,
    chat: chatReducer,
  },
  middleware: (getDefault) =>
    getDefault({
      serializableCheck: {
        // File objects are non-serializable; they're only carried in a single
        // thunk payload (uploadFilesThunk arg) and never land in the store.
        ignoredActions: ['ui/upload/pending', 'ui/upload/fulfilled', 'ui/upload/rejected'],
        ignoredActionPaths: ['meta.arg.files'],
      },
    }),
})

// Persist event log entries + chat history so they survive page refreshes and
// browser restarts. Throttled: log records can arrive dozens per second, and
// JSON.stringify of large arrays on the hot path would jank the UI. Only writes
// on identity change (reducers return new refs on update) so idle state == idle
// disk.
let saveScheduled = false
let lastEntries: unknown = null
let lastFilter: unknown = null
let lastChatMessages: unknown = null
store.subscribe(() => {
  const state = store.getState()
  const eventsChanged =
    state.events.entries !== lastEntries || state.events.filter !== lastFilter
  const chatChanged = state.chat.messages !== lastChatMessages
  if (!eventsChanged && !chatChanged) return
  lastEntries = state.events.entries
  lastFilter = state.events.filter
  lastChatMessages = state.chat.messages
  if (saveScheduled) return
  saveScheduled = true
  window.setTimeout(() => {
    saveScheduled = false
    const s = store.getState()
    persistEvents(s.events.entries, s.events.filter)
    persistChat(s.chat.messages)
  }, 500)
})

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch
