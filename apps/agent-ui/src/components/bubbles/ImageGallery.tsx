import type { QueryImage } from '../../api'

export function ImageGallery({ images }: { images: QueryImage[] }) {
  if (images.length === 0) return null
  return (
    <div className="images">
      {images.map((img) => (
        <a
          key={img.image_key}
          href={img.url}
          target="_blank"
          rel="noreferrer"
          title={img.caption ?? img.image_key}
        >
          <img src={img.url} alt={img.caption ?? img.image_key} />
        </a>
      ))}
    </div>
  )
}
