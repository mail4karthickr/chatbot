import type { Citation, QueryImage } from '../../api'

export type Message =
  | { role: 'user'; text: string }
  | { role: 'bot'; text: string; citations: Citation[]; images: QueryImage[] }
  | { role: 'bot-error'; text: string }
