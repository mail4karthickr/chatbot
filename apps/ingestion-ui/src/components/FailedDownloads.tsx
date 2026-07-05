import { useAppSelector } from '../app/hooks'

export function FailedDownloads() {
  const result = useAppSelector((s) => s.ingest.result)
  if (!result || result.failed.length === 0) return null

  return (
    <details className="failed">
      <summary>
        {result.failed.length} failed download
        {result.failed.length === 1 ? '' : 's'}
      </summary>
      <ul>
        {result.failed.map((f) => (
          <li key={f.key}>
            <code>{f.key}</code> — {f.error}
          </li>
        ))}
      </ul>
    </details>
  )
}
