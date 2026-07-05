import { createAsyncThunk, createSlice } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'
import { ingestAll } from '../../api'
import type { IngestResponse } from '../../api'

export type IngestStatus = 'idle' | 'running' | 'succeeded' | 'failed'

export type IngestState = {
  status: IngestStatus
  result: IngestResponse | null
  error: string | null
}

const initialState: IngestState = {
  status: 'idle',
  result: null,
  error: null,
}

export const runIngest = createAsyncThunk(
  'ingest/run',
  async (_: void, { rejectWithValue }) => {
    try {
      return await ingestAll()
    } catch (err) {
      return rejectWithValue(err instanceof Error ? err.message : 'Ingest failed')
    }
  },
)

const ingestSlice = createSlice({
  name: 'ingest',
  initialState,
  reducers: {
    clearIngest: (state) => {
      state.result = null
      state.error = null
      state.status = 'idle'
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(runIngest.pending, (state) => {
        state.status = 'running'
        state.error = null
        state.result = null
      })
      .addCase(runIngest.fulfilled, (state, action) => {
        state.status = 'succeeded'
        state.result = action.payload
      })
      .addCase(runIngest.rejected, (state, action: PayloadAction<unknown>) => {
        state.status = 'failed'
        state.error = typeof action.payload === 'string' ? action.payload : 'Ingest failed'
      })
  },
})

export const { clearIngest } = ingestSlice.actions
export default ingestSlice.reducer
