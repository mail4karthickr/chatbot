import { useAppDispatch, useAppSelector } from '../app/hooks'
import { runIngest } from '../features/ingest/ingestSlice'
import { fetchS3Files } from '../features/s3/s3Slice'
import { openModal } from '../features/ui/uiSlice'

export function Header() {
  const dispatch = useAppDispatch()
  const listStatus = useAppSelector((s) => s.s3.status)
  const ingestStatus = useAppSelector((s) => s.ingest.status)
  const count = useAppSelector((s) => s.s3.files?.length ?? 0)

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
          onClick={() => dispatch(runIngest())}
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
