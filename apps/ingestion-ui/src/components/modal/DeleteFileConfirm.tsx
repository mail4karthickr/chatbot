import { useAppDispatch, useAppSelector } from '../../app/hooks'
import { closeModal, deleteFileThunk } from '../../features/ui/uiSlice'
import { formatBytes } from '../../utils/format'

export function DeleteFileConfirm({
  fileKey,
  name,
  size,
  bucket,
}: {
  fileKey: string
  name: string
  size: number
  bucket: string
}) {
  const dispatch = useAppDispatch()
  const busy = useAppSelector((s) => s.ui.busy)
  const error = useAppSelector((s) => s.ui.error)

  return (
    <div className="modal-form">
      <div className="warning">
        This will permanently delete <b>{name}</b> ({formatBytes(size)}):
      </div>
      <div className="path-hint danger-hint">
        <code>s3://{bucket}/{fileKey}</code>
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
          onClick={() => dispatch(deleteFileThunk({ key: fileKey, name }))}
          disabled={busy}
        >
          {busy ? 'Deleting…' : 'Delete permanently'}
        </button>
      </div>
    </div>
  )
}
