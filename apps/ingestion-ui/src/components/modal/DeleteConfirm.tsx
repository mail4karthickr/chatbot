import { useAppDispatch, useAppSelector } from '../../app/hooks'
import { closeModal, deleteFolderThunk } from '../../features/ui/uiSlice'
import { formatBytes } from '../../utils/format'

export function DeleteConfirm({
  path,
  fileCount,
  totalSize,
  bucket,
}: {
  path: string
  fileCount: number
  totalSize: number
  bucket: string
}) {
  const dispatch = useAppDispatch()
  const busy = useAppSelector((s) => s.ui.busy)
  const error = useAppSelector((s) => s.ui.error)

  return (
    <div className="modal-form">
      <div className="warning">
        This will permanently delete <b>{fileCount}</b> file
        {fileCount === 1 ? '' : 's'} ({formatBytes(totalSize)}) under:
      </div>
      <div className="path-hint danger-hint">
        <code>s3://{bucket}/{path}/</code>
      </div>
      {error && <div className="modal-error">{error}</div>}
      <div className="modal-actions">
        <button
          type="button"
          className="btn secondary"
          onClick={() => dispatch(closeModal())}
          disabled={busy}
        >
          Cancel
        </button>
        <button
          type="button"
          className="btn danger"
          onClick={() => dispatch(deleteFolderThunk(path))}
          disabled={busy}
        >
          {busy ? 'Deleting…' : 'Delete permanently'}
        </button>
      </div>
    </div>
  )
}
