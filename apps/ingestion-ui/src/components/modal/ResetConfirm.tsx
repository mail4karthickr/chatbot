import { useState } from 'react'
import { useAppDispatch, useAppSelector } from '../../app/hooks'
import { closeModal, resetAllThunk } from '../../features/ui/uiSlice'

export function ResetConfirm() {
  const dispatch = useAppDispatch()
  const busy = useAppSelector((s) => s.ui.busy)
  const error = useAppSelector((s) => s.ui.error)
  const [ack, setAck] = useState('')
  const canSubmit = ack.trim().toUpperCase() === 'RESET' && !busy

  return (
    <form
      className="modal-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (canSubmit) dispatch(resetAllThunk())
      }}
    >
      <div className="warning">
        This will <b>wipe every row</b> from the sync-service ledger and{' '}
        <b>drop the Qdrant collection</b>. Your S3 files are untouched — the next
        ingest will re-process every file under <code>docs/</code> from scratch.
      </div>
      <div className="warning">
        Testing utility only. There is no undo.
      </div>
      <label className="field">
        <span className="field-label">Type <b>RESET</b> to confirm</span>
        <input
          className="text-input"
          autoFocus
          value={ack}
          onChange={(e) => setAck(e.target.value)}
          placeholder="RESET"
          disabled={busy}
        />
      </label>
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
        <button type="submit" className="btn danger" disabled={!canSubmit}>
          {busy ? 'Resetting…' : 'Reset everything'}
        </button>
      </div>
    </form>
  )
}
