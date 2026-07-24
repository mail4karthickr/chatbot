import { createAsyncThunk, createSlice } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'
import { listDocuments } from '../../api'
import type { DocSummary } from '../../api'
import type { LoadStatus } from '../s3/s3Slice'

// Document catalog: doc_id -> routing summary, served by GET /documents.
// Refreshed on mount, on manual Refresh, and when an ingest job finishes
// (that's when summaries are created or replaced).
export type CatalogState = {
  byDocId: Record<string, DocSummary>
  status: LoadStatus
  error: string | null
}

const initialState: CatalogState = {
  byDocId: {},
  status: 'idle',
  error: null,
}

export const fetchCatalog = createAsyncThunk(
  'catalog/fetch',
  async (_: void, { rejectWithValue }) => {
    try {
      return await listDocuments()
    } catch (err) {
      return rejectWithValue(err instanceof Error ? err.message : 'Failed to list documents')
    }
  },
)

const catalogSlice = createSlice({
  name: 'catalog',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(fetchCatalog.pending, (state) => {
        state.status = 'loading'
        state.error = null
      })
      .addCase(fetchCatalog.fulfilled, (state, action) => {
        state.status = 'succeeded'
        state.byDocId = Object.fromEntries(
          action.payload.documents.map((d) => [d.doc_id, d]),
        )
      })
      .addCase(fetchCatalog.rejected, (state, action: PayloadAction<unknown>) => {
        state.status = 'failed'
        state.error = typeof action.payload === 'string' ? action.payload : 'Failed to list documents'
      })
  },
})

export default catalogSlice.reducer
