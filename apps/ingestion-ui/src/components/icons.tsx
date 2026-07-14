export function ChevronIcon({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 16 16" width="12" height="12"
      className="chev" style={{ transform: open ? 'rotate(90deg)' : 'none' }}
      aria-hidden="true"
    >
      <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function FolderIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" className="ic-folder" aria-hidden="true">
      <path d="M1.5 4.25A1.75 1.75 0 013.25 2.5h3.19l1.5 1.5H12.75c.966 0 1.75.784 1.75 1.75v5.5A1.75 1.75 0 0112.75 13H3.25a1.75 1.75 0 01-1.75-1.75V4.25z" fill="currentColor" />
    </svg>
  )
}

export function BucketIcon() {
  return (
    <svg viewBox="0 0 16 16" width="16" height="16" className="ic-bucket" aria-hidden="true">
      <ellipse cx="8" cy="3.5" rx="6" ry="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M2 3.5v9c0 .83 2.69 1.5 6 1.5s6-.67 6-1.5v-9" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
      <path d="M2 7.5c0 .83 2.69 1.5 6 1.5s6-.67 6-1.5" fill="none" stroke="currentColor" strokeWidth="1.1" opacity="0.6" />
      <path d="M2 10.5c0 .83 2.69 1.5 6 1.5s6-.67 6-1.5" fill="none" stroke="currentColor" strokeWidth="1.1" opacity="0.4" />
    </svg>
  )
}

export function UploadIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M8 11V2M4 5.5L8 1.5l4 4M2.5 12v1.5A1 1 0 003.5 14.5h9a1 1 0 001-1V12" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function NewFolderIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M1.5 4.25A1.75 1.75 0 013.25 2.5h3.19l1.5 1.5H12.75c.966 0 1.75.784 1.75 1.75v5.5A1.75 1.75 0 0112.75 13H3.25a1.75 1.75 0 01-1.75-1.75V4.25z" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M8 7v4M6 9h4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  )
}

export function TrashIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M3 4h10M6 4V2.5A.5.5 0 016.5 2h3a.5.5 0 01.5.5V4M4 4l.7 9.1a1 1 0 001 .9h4.6a1 1 0 001-.9L12 4M6.5 7v5M9.5 7v5" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function FileIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" className="ic-file" aria-hidden="true">
      <path d="M4 1.5h5.25L13 5.25V14a.5.5 0 01-.5.5h-8a.5.5 0 01-.5-.5V2a.5.5 0 01.5-.5z"
        fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M9 1.5V5h4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
    </svg>
  )
}

export function DownloadIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M8 2v8m0 0l-3.2-3.2M8 10l3.2-3.2"
        fill="none" stroke="currentColor" strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" />
      <path d="M2.5 12v1.5A1 1 0 003.5 14.5h9a1 1 0 001-1V12"
        fill="none" stroke="currentColor" strokeWidth="1.6"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export function DocScanIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M4 1.5h5.25L13 5.25V14a.5.5 0 01-.5.5h-8a.5.5 0 01-.5-.5V2a.5.5 0 01.5-.5z"
        fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M9 1.5V5h4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <path d="M6 8h4M6 10h4M6 12h2.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" />
    </svg>
  )
}

export function EyeIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M1.5 8s2.5-4.5 6.5-4.5S14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z"
        fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" />
      <circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  )
}

export function EmptyBucketIcon() {
  return (
    <svg viewBox="0 0 24 24" width="40" height="40">
      <ellipse cx="12" cy="5" rx="9" ry="2.25" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M3 5v14c0 1.24 4.03 2.25 9 2.25s9-1.01 9-2.25V5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <path d="M3 11c0 1.24 4.03 2.25 9 2.25s9-1.01 9-2.25" fill="none" stroke="currentColor" strokeWidth="1.2" opacity="0.55" />
      <path d="M3 16c0 1.24 4.03 2.25 9 2.25s9-1.01 9-2.25" fill="none" stroke="currentColor" strokeWidth="1.2" opacity="0.35" />
    </svg>
  )
}
