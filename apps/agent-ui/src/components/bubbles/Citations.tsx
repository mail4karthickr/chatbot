import type { Citation } from '../../api'

export function Citations({ citations }: { citations: Citation[] }) {
  if (citations.length === 0) return null
  return (
    <details className="citations">
      <summary>
        {citations.length} citation{citations.length === 1 ? '' : 's'}
      </summary>
      <ol>
        {citations.map((c) => (
          <li key={c.n}>
            <span className="cite-tag">[{c.n}]</span> page {c.page} · {c.kind} ·{' '}
            <code>{c.chunk_id}</code>
          </li>
        ))}
      </ol>
    </details>
  )
}
