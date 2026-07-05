import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const AGENT_TARGET = process.env.VITE_AGENT_TARGET ?? 'http://localhost:8001'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/chat': { target: AGENT_TARGET, changeOrigin: true },
    },
  },
})
