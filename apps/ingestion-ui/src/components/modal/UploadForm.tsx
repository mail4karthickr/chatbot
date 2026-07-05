import { useRef, useState } from 'react'
import { useAppDispatch, useAppSelector } from '../../app/hooks'
import { closeModal, uploadFilesThunk } from '../../features/ui/uiSlice'
import { formatBytes } from '../../utils/format'
import { PathHint } from './Modal'

export function UploadForm({ targetPath, bucket }: { targetPath: string; bucket: string }) {
  const dispatch = useAppDispatch()
  const busy = useAppSelector((s) => s.ui.busy)
  const error = useAppSelector((s) => s.ui.error)
  const [picked, setPicked] = useState<File[]>([])
  const inputRef = useRef<HTMLInputElement>(null)

  const canSubmit = picked.length > 0 && !busy

  return (
    <form
      className="modal-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (canSubmit) dispatch(uploadFilesThunk({ target: targetPath, files: picked }))
      }}
    >
      <PathHint path={targetPath} bucket={bucket} />
      <label className="field">
        <span className="field-label">Files</span>
        <input
          ref={inputRef}
          type="file"
          multiple
          onChange={(e) => setPicked(Array.from(e.target.files ?? []))}
          disabled={busy}
        />
      </label>
      {picked.length > 0 && (
        <ul className="pick-list">
          {picked.map((f, i) => (
            <li key={i}>
              <span className="mono">{f.name}</span>
              <span className="muted">{formatBytes(f.size)}</span>
            </li>
          ))}
        </ul>
      )}
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
        <button type="submit" className="btn primary" disabled={!canSubmit}>
          {busy ? 'Uploading…' : `Upload ${picked.length || ''}`.trim()}
        </button>
      </div>
    </form>
  )
}
