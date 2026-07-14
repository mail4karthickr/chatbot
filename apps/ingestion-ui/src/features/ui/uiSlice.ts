import { createAsyncThunk, createSlice } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'
import {
  createFolder,
  deleteFile,
  deleteFolder,
  resetAll,
  uploadFiles,
} from '../../api'
import type { UploadResponse } from '../../api'
import { fetchS3Files } from '../s3/s3Slice'
import { clearIngest } from '../ingest/ingestSlice'

export type ModalState =
  | { kind: 'upload'; targetPath: string }
  | { kind: 'newFolder'; parentPath: string }
  | { kind: 'delete'; path: string; fileCount: number; totalSize: number }
  | { kind: 'deleteFile'; key: string; name: string; size: number }
  | { kind: 'imagePreview'; key: string; name: string }
  | { kind: 'parsePreview'; key: string; name: string }
  | { kind: 'reset' }
  | null

export type UIState = {
  modal: ModalState
  busy: boolean
  error: string | null
  flash: string | null
}

const initialState: UIState = {
  modal: null,
  busy: false,
  error: null,
  flash: null,
}

const FLASH_MS = 4000
let flashTimer: ReturnType<typeof setTimeout> | null = null

export const showFlash = createAsyncThunk<void, string>(
  'ui/showFlash',
  async (message, { dispatch }) => {
    dispatch(uiSlice.actions.setFlash(message))
    if (flashTimer) clearTimeout(flashTimer)
    flashTimer = setTimeout(() => {
      dispatch(uiSlice.actions.setFlash(null))
      flashTimer = null
    }, FLASH_MS)
  },
)

export const uploadFilesThunk = createAsyncThunk<
  UploadResponse,
  { target: string; files: File[] }
>(
  'ui/upload',
  async ({ target, files }, { dispatch, rejectWithValue }) => {
    try {
      const res = await uploadFiles(target, files)
      if (res.failed.length === 0) {
        dispatch(fetchS3Files())
        dispatch(
          showFlash(
            `Uploaded ${res.uploaded.length} file${res.uploaded.length === 1 ? '' : 's'}.`,
          ),
        )
      }
      return res
    } catch (err) {
      return rejectWithValue(err instanceof Error ? err.message : 'Upload failed')
    }
  },
)

export const createFolderThunk = createAsyncThunk<
  string,
  { parentPath: string; name: string }
>(
  'ui/createFolder',
  async ({ parentPath, name }, { dispatch, rejectWithValue }) => {
    const path = parentPath ? `${parentPath}/${name}` : name
    try {
      await createFolder(path)
      dispatch(fetchS3Files())
      dispatch(showFlash(`Created folder ${path}/`))
      return path
    } catch (err) {
      return rejectWithValue(err instanceof Error ? err.message : 'Create folder failed')
    }
  },
)

export const deleteFolderThunk = createAsyncThunk<
  { path: string; count: number },
  string
>(
  'ui/deleteFolder',
  async (path, { dispatch, rejectWithValue }) => {
    try {
      const res = await deleteFolder(path)
      dispatch(fetchS3Files())
      dispatch(
        showFlash(
          `Deleted ${res.count} file${res.count === 1 ? '' : 's'} under ${path}/.`,
        ),
      )
      return { path, count: res.count }
    } catch (err) {
      return rejectWithValue(err instanceof Error ? err.message : 'Delete failed')
    }
  },
)

export const deleteFileThunk = createAsyncThunk<
  string,
  { key: string; name: string }
>(
  'ui/deleteFile',
  async ({ key, name }, { dispatch, rejectWithValue }) => {
    try {
      await deleteFile(key)
      dispatch(fetchS3Files())
      dispatch(showFlash(`Deleted ${name}.`))
      return key
    } catch (err) {
      return rejectWithValue(err instanceof Error ? err.message : 'Delete failed')
    }
  },
)

export const resetAllThunk = createAsyncThunk<{ ledger_rows_removed: number }, void>(
  'ui/reset',
  async (_, { dispatch, rejectWithValue }) => {
    try {
      const res = await resetAll()
      dispatch(clearIngest())
      // /reset also sweeps S3 image artifacts, so the file tree is now stale.
      // Re-fetch so the UI reflects the new (post-cleanup) bucket contents.
      dispatch(fetchS3Files())
      dispatch(
        showFlash(
          `Reset done — ${res.ledger_rows_removed} ledger row${res.ledger_rows_removed === 1 ? '' : 's'} removed, ${res.artifacts_removed} artifact${res.artifacts_removed === 1 ? '' : 's'} deleted, Qdrant recreated.`,
        ),
      )
      return { ledger_rows_removed: res.ledger_rows_removed }
    } catch (err) {
      return rejectWithValue(err instanceof Error ? err.message : 'Reset failed')
    }
  },
)

const uiSlice = createSlice({
  name: 'ui',
  initialState,
  reducers: {
    openModal: (state, action: PayloadAction<Exclude<ModalState, null>>) => {
      state.modal = action.payload
      state.error = null
    },
    closeModal: (state) => {
      if (state.busy) return
      state.modal = null
      state.error = null
    },
    setFlash: (state, action: PayloadAction<string | null>) => {
      state.flash = action.payload
    },
  },
  extraReducers: (builder) => {
    const startBusy = (state: UIState) => {
      state.busy = true
      state.error = null
    }
    const rejectBusy = (state: UIState, action: PayloadAction<unknown>) => {
      state.busy = false
      state.error =
        typeof action.payload === 'string' ? action.payload : 'Request failed'
    }
    const finishSuccess = (state: UIState) => {
      state.busy = false
      state.modal = null
    }

    builder
      .addCase(uploadFilesThunk.pending, startBusy)
      .addCase(uploadFilesThunk.fulfilled, (state, action) => {
        const res = action.payload
        state.busy = false
        if (res.failed.length > 0) {
          state.error = `${res.failed.length} file(s) failed: ${res.failed[0].error}`
        } else {
          state.modal = null
        }
      })
      .addCase(uploadFilesThunk.rejected, rejectBusy)

      .addCase(createFolderThunk.pending, startBusy)
      .addCase(createFolderThunk.fulfilled, finishSuccess)
      .addCase(createFolderThunk.rejected, rejectBusy)

      .addCase(deleteFolderThunk.pending, startBusy)
      .addCase(deleteFolderThunk.fulfilled, finishSuccess)
      .addCase(deleteFolderThunk.rejected, rejectBusy)

      .addCase(deleteFileThunk.pending, startBusy)
      .addCase(deleteFileThunk.fulfilled, finishSuccess)
      .addCase(deleteFileThunk.rejected, rejectBusy)

      .addCase(resetAllThunk.pending, startBusy)
      .addCase(resetAllThunk.fulfilled, finishSuccess)
      .addCase(resetAllThunk.rejected, rejectBusy)
  },
})

export const { openModal, closeModal, setFlash } = uiSlice.actions
export default uiSlice.reducer
