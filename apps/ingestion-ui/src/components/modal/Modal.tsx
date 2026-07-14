import { useEffect } from 'react'

export function Modal({
  title,
  onClose,
  children,
  size = 'sm',
}: {
  title: string
  onClose: () => void
  children: React.ReactNode
  size?: 'sm' | 'lg'
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className={`modal-card modal-${size}`}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2>{title}</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

export function PathHint({ path, bucket }: { path: string; bucket: string }) {
  const display = path ? `${bucket}/${path}` : bucket
  return (
    <div className="path-hint">
      <span className="path-hint-label">Destination</span>
      <code>s3://{display}/</code>
    </div>
  )
}
