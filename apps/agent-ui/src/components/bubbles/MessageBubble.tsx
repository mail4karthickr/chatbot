import type { Message } from '../../features/chat/types'
import { BotBubble } from './BotBubble'
import { ErrorBubble } from './ErrorBubble'
import { UserBubble } from './UserBubble'

export function MessageBubble({ message }: { message: Message }) {
  switch (message.role) {
    case 'user':
      return <UserBubble text={message.text} />
    case 'bot-error':
      return <ErrorBubble text={message.text} />
    case 'bot':
      return (
        <BotBubble
          text={message.text}
          citations={message.citations}
          images={message.images}
        />
      )
  }
}
