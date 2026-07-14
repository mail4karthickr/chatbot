import { useState } from 'react'
import { s3ObjectUrl } from '../../api'

export function ImagePreview({
  fileKey,
  name,
  bucket,
}: {
  fileKey: string
  name: string
  bucket: string
}) {
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const src = s3ObjectUrl(fileKey)

  return (
    <div className="image-preview">
      <div className="path-hint">
        <code>s3://{bucket}/{fileKey}</code>
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
      </div>
    </div>
  )
}
