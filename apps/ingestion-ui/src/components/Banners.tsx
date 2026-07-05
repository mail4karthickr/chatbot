import { useAppSelector } from '../app/hooks'
import { formatBytes } from '../utils/format'

export function Banners() {
  const listError = useAppSelector((s) => s.s3.error)
  const ingestError = useAppSelector((s) => s.ingest.error)
  const result = useAppSelector((s) => s.ingest.result)

  return (
    <>
      {listError && <div className="banner error">List error: {listError}</div>}
      {ingestError && <div className="banner error">Ingest error: {ingestError}</div>}
      {result && (
        <div className="banner ok">
          Downloaded <b>{result.downloaded.length}</b> file
          {result.downloaded.length === 1 ? '' : 's'} ({formatBytes(result.total_bytes)}) to{' '}
          <code>{result.dest_dir}</code>
          {result.failed.length > 0 && (
            <>
              {' '}
              · <span className="err-inline">{result.failed.length} failed</span>
            </>
          )}
        </div>
      )}
    </>
  )
}
