import { useState } from 'react'
import { useAppDispatch, useAppSelector } from '../../app/hooks'
import { closeModal, createFolderThunk } from '../../features/ui/uiSlice'
import { PathHint } from './Modal'

export function NewFolderForm({ parentPath, bucket }: { parentPath: string; bucket: string }) {
  const dispatch = useAppDispatch()
  const busy = useAppSelector((s) => s.ui.busy)
  const error = useAppSelector((s) => s.ui.error)
  const [name, setName] = useState('')
  const trimmed = name.trim().replace(/^\/+|\/+$/g, '')
  const canSubmit = trimmed.length > 0 && !busy

  return (
    <form
      className="modal-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (canSubmit) dispatch(createFolderThunk({ parentPath, name: trimmed }))
      }}
    >
      <PathHint path={parentPath} bucket={bucket} />
      <label className="field">
        <span className="field-label">Folder name</span>
        <input
          className="text-input"
          autoFocus
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. medical_study"
          disabled={busy}
        />
      </label>
      {trimmed && (
        <div className="preview">
          Will create <code>s3://{bucket}/{parentPath ? parentPath + '/' : ''}{trimmed}/</code>
        </div>
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
          {busy ? 'Creating…' : 'Create folder'}
        </button>
      </div>
    </form>
  )
}
