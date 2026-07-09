import { useAppDispatch, useAppSelector } from '../app/hooks'
import { clearEntries, startStreaming } from '../features/events/eventsSlice'
import { runIngest } from '../features/ingest/ingestSlice'
import { fetchS3Files } from '../features/s3/s3Slice'
import { openModal } from '../features/ui/uiSlice'

export function Header() {
  const dispatch = useAppDispatch()
  const listStatus = useAppSelector((s) => s.s3.status)
  const ingestStatus = useAppSelector((s) => s.ingest.status)
  // Only source documents under docs/ get ingested — mirrors INGEST_PREFIX in
  // apps/ingestion-service/app.py. Excludes _artifacts/ (pipeline outputs).
  const count = useAppSelector(
    (s) => s.s3.files?.filter((f) => f.key.startsWith('docs/')).length ?? 0,
  )

  const loadingList = listStatus === 'loading'
  const ingesting = ingestStatus === 'running'

  return (
    <header className="header">
      <div className="header-left">
        <h1>S3 Ingestion</h1>
        <p className="subtitle">
          Browse the bucket and download every file to local storage.
        </p>
      </div>
      <div className="header-actions">
        <button
          className="btn danger"
          title="Wipe the sync-service ledger and Qdrant collection. S3 files are untouched."
          onClick={() => dispatch(openModal({ kind: 'reset' }))}
          disabled={ingesting || loadingList}
        >
          Reset
        </button>
        <button
          className="btn secondary"
          onClick={() => dispatch(fetchS3Files())}
          disabled={loadingList || ingesting}
        >
          {loadingList ? 'Refreshing…' : 'Refresh'}
        </button>
        <button
          className="btn primary"
          onClick={() => {
            // Fresh log view for each ingest run, then open the SSE stream
            // BEFORE firing the request so we capture the initial API events.
            dispatch(clearEntries())
            dispatch(startStreaming())
            dispatch(runIngest())
          }}
          disabled={ingesting || loadingList || count === 0}
        >
          {ingesting
            ? 'Ingesting…'
            : `Ingest ${count} file${count === 1 ? '' : 's'}`}
        </button>
      </div>
    </header>
  )
}
