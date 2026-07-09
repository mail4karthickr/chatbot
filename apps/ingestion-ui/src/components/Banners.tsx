import { useAppSelector } from '../app/hooks'

// Success details go through the live log stream; only surface errors here.
export function Banners() {
  const listError = useAppSelector((s) => s.s3.error)
  const ingestError = useAppSelector((s) => s.ingest.error)

  return (
    <>
      {listError && <div className="banner error">List error: {listError}</div>}
      {ingestError && <div className="banner error">Ingest error: {ingestError}</div>}
    </>
  )
}
