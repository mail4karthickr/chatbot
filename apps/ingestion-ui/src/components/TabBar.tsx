export type Tab = 'documents' | 'chat'

/**
 * Top-level nav between the Documents (S3 + ingestion + live logs) view and
 * the Chat (retrieval-only query) view. Hash-synced by the parent so refresh
 * preserves the tab and the browser back button navigates between them.
 */
export function TabBar({
  current,
  onSelect,
}: {
  current: Tab
  onSelect: (t: Tab) => void
}) {
  const tabs: { key: Tab; label: string; hint: string }[] = [
    { key: 'documents', label: 'Documents', hint: 'Browse S3, ingest, watch live logs' },
    { key: 'chat', label: 'Chat', hint: 'Ask the knowledge base' },
  ]
  return (
    <nav className="tab-bar" role="tablist" aria-label="Primary">
      <div className="tab-brand">Ingestion Console</div>
      <div className="tab-list">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={current === t.key}
            className={`tab-btn ${current === t.key ? 'active' : ''}`}
            title={t.hint}
            onClick={() => onSelect(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
    </nav>
  )
}
