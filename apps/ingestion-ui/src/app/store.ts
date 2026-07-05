import { configureStore } from '@reduxjs/toolkit'
import s3Reducer from '../features/s3/s3Slice'
import ingestReducer from '../features/ingest/ingestSlice'
import uiReducer from '../features/ui/uiSlice'

export const store = configureStore({
  reducer: {
    s3: s3Reducer,
    ingest: ingestReducer,
    ui: uiReducer,
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

export type RootState = ReturnType<typeof store.getState>
export type AppDispatch = typeof store.dispatch
