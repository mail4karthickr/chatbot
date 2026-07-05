import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const INGEST_TARGET = process.env.VITE_INGEST_TARGET ?? 'http://localhost:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      '/s3': { target: INGEST_TARGET, changeOrigin: true },
      '/ingest': { target: INGEST_TARGET, changeOrigin: true },
    },
  },
})
