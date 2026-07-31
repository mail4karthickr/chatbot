import { useEffect, useMemo, useState } from 'react'
import { s3ObjectUrl } from '../../api'
import { useAppDispatch, useAppSelector } from '../../app/hooks'
import { openModal } from '../../features/ui/uiSlice'
import { isImageName } from '../../utils/format'

export function ImagePreview({
  fileKey,
  name,
  bucket,
}: {
  fileKey: string
  name: string
  bucket: string
}) {
  const dispatch = useAppDispatch()
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const src = s3ObjectUrl(fileKey)

  // The component stays mounted while Prev/Next swap fileKey underneath it,
  // so the load state must reset by hand or the old image's 'ready' leaks.
  useEffect(() => setStatus('loading'), [fileKey])

  // Every image in the bucket, key-sorted — matches the tree's per-folder
  // alphabetical order, so Next walks the same order the user sees.
  const files = useAppSelector((s) => s.s3.files)
  const imageKeys = useMemo(
    () =>
      (files ?? [])
        .map((f) => f.key)
        .filter((k) => isImageName(k.split('/').pop() ?? ''))
        .sort((a, b) => a.localeCompare(b)),
    [files],
  )
  const idx = imageKeys.indexOf(fileKey)

  const goTo = (target: string | undefined) => {
    if (!target) return
    dispatch(
      openModal({
        kind: 'imagePreview',
        key: target,
        name: target.split('/').pop() ?? target,
      }),
    )
  }
  const prevKey = idx > 0 ? imageKeys[idx - 1] : undefined
  const nextKey =
    idx >= 0 && idx < imageKeys.length - 1 ? imageKeys[idx + 1] : undefined

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') goTo(prevKey)
      if (e.key === 'ArrowRight') goTo(nextKey)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prevKey, nextKey])

  return (
    <div className="image-preview">
      <div className="path-hint">
        <code>s3://{bucket}/{fileKey}</code>
        {idx >= 0 && imageKeys.length > 1 && (
          <span className="image-preview-counter">
            {idx + 1} / {imageKeys.length}
          </span>
        )}
      </div>
      <div className="image-preview-frame">
        {status === 'loading' && (
          <div className="image-preview-status">Loading…</div>
        )}
        {status === 'error' && (
          <div className="image-preview-status error">
            Couldn't load this image.
          </div>
        )}
        <img
          src={src}
          alt={name}
          className="image-preview-img"
          style={{ display: status === 'ready' ? 'block' : 'none' }}
          onLoad={() => setStatus('ready')}
          onError={() => setStatus('error')}
        />
        {imageKeys.length > 1 && (
          <>
            <button
              type="button"
              className="image-preview-nav prev"
              onClick={() => goTo(prevKey)}
              disabled={!prevKey}
              title="Previous image (←)"
              aria-label="Previous image"
            >
              <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
                <path d="M10 4L6 8l4 4" fill="none" stroke="currentColor"
                  strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <button
              type="button"
              className="image-preview-nav next"
              onClick={() => goTo(nextKey)}
              disabled={!nextKey}
              title="Next image (→)"
              aria-label="Next image"
            >
              <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
                <path d="M6 4l4 4-4 4" fill="none" stroke="currentColor"
                  strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </>
        )}
      </div>
    </div>
  )
}
