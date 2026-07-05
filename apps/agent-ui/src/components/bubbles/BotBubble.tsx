import type { Citation, QueryImage } from '../../api'
import { Citations } from './Citations'
import { ImageGallery } from './ImageGallery'

export function BotBubble({
  text,
  citations,
  images,
}: {
  text: string
  citations: Citation[]
  images: QueryImage[]
}) {
  return (
    <div className="bubble bot">
      <div className="answer">{text}</div>
      <ImageGallery images={images} />
      <Citations citations={citations} />
    </div>
  )
}
