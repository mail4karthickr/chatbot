import { useState } from 'react'
import { useAppDispatch, useAppSelector } from '../app/hooks'
import { sendMessage } from '../features/chat/chatSlice'

export function Composer() {
  const dispatch = useAppDispatch()
  const pending = useAppSelector((s) => s.chat.pending)
  const [input, setInput] = useState('')

  function submit() {
    const q = input.trim()
    if (!q || pending) return
    setInput('')
    dispatch(sendMessage(q))
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="composer">
      <textarea
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask a question…"
        rows={2}
        disabled={pending}
      />
      <button onClick={submit} disabled={pending || !input.trim()}>
        Send
      </button>
    </div>
  )
}
