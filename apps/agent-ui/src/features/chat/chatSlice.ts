import { createAsyncThunk, createSlice } from '@reduxjs/toolkit'
import type { PayloadAction } from '@reduxjs/toolkit'
import { askAgent } from '../../api'
import type { ChatResponse, ChatTurn } from '../../api'
import type { Message } from './types'

export type ChatState = {
  messages: Message[]
  pending: boolean
}

const initialState: ChatState = {
  messages: [],
  pending: false,
}

function toHistory(messages: Message[]): ChatTurn[] {
  return messages
    .filter((m): m is Extract<Message, { role: 'user' | 'bot' }> =>
      m.role === 'user' || m.role === 'bot',
    )
    .map((m) => ({
      role: m.role === 'user' ? 'user' : 'assistant',
      content: m.text,
    }))
}

export const sendMessage = createAsyncThunk<
  ChatResponse,
  string,
  { state: { chat: ChatState }; rejectValue: string }
>('chat/send', async (text, { getState, rejectWithValue }) => {
  const history = toHistory(getState().chat.messages)
  try {
    return await askAgent(text, history)
  } catch (err) {
    return rejectWithValue(err instanceof Error ? err.message : 'Request failed')
  }
})

const chatSlice = createSlice({
  name: 'chat',
  initialState,
  reducers: {
    clearChat: (state) => {
      state.messages = []
      state.pending = false
    },
  },
  extraReducers: (builder) => {
    builder
      .addCase(sendMessage.pending, (state, action) => {
        state.messages.push({ role: 'user', text: action.meta.arg })
        state.pending = true
      })
      .addCase(sendMessage.fulfilled, (state, action) => {
        state.pending = false
        state.messages.push({
          role: 'bot',
          text: action.payload.answer,
          citations: action.payload.citations ?? [],
          images: action.payload.images ?? [],
        })
      })
      .addCase(sendMessage.rejected, (state, action: PayloadAction<string | undefined>) => {
        state.pending = false
        state.messages.push({
          role: 'bot-error',
          text: action.payload ?? 'Request failed',
        })
      })
  },
})

export const { clearChat } = chatSlice.actions
export default chatSlice.reducer
