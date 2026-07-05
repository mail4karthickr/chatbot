import { useEffect, useRef } from 'react'
import { useAppSelector } from '../app/hooks'
import { MessageBubble } from './bubbles/MessageBubble'
import { EmptyState } from './EmptyState'
import { TypingIndicator } from './TypingIndicator'

export function MessageList() {
  const messages = useAppSelector((s) => s.chat.messages)
  const pending = useAppSelector((s) => s.chat.pending)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [messages, pending])

  return (
    <div className="messages" ref={scrollRef}>
      {messages.length === 0 && !pending && <EmptyState />}
      {messages.map((m, i) => (
        <MessageBubble key={i} message={m} />
      ))}
      {pending && <TypingIndicator />}
    </div>
  )
}
