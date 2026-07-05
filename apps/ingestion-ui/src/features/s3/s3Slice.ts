import { createAsyncThunk, createSlice } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'
import { listS3Files } from '../../api'
import type { S3File } from '../../api'

export type LoadStatus = 'idle' | 'loading' | 'succeeded' | 'failed'

export type S3State = {
  bucket: string | null
  files: S3File[] | null
  folders: string[]
  status: LoadStatus
  error: string | null
}

const initialState: S3State = {
  bucket: null,
  files: null,
  folders: [],
  status: 'idle',
  error: null,
}

export const fetchS3Files = createAsyncThunk(
  's3/fetch',
  async (_: void, { rejectWithValue }) => {
    try {
      return await listS3Files()
    } catch (err) {
      return rejectWithValue(err instanceof Error ? err.message : 'Failed to list files')
    }
  },
)

const s3Slice = createSlice({
  name: 's3',
  initialState,
  reducers: {},
  extraReducers: (builder) => {
    builder
      .addCase(fetchS3Files.pending, (state) => {
        state.status = 'loading'
        state.error = null
      })
      .addCase(fetchS3Files.fulfilled, (state, action) => {
        state.status = 'succeeded'
        state.bucket = action.payload.bucket
        state.files = action.payload.files
        state.folders = action.payload.folders ?? []
      })
      .addCase(fetchS3Files.rejected, (state, action: PayloadAction<unknown>) => {
        state.status = 'failed'
        state.error = typeof action.payload === 'string' ? action.payload : 'Failed to list files'
        state.files = null
        state.folders = []
      })
  },
})

export default s3Slice.reducer
