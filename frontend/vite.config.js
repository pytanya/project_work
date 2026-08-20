import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  optimizeDeps: {
    include: ['katex'],
  },
  server: {
    proxy: {
      // REST + WebSocket → локальный бэкенд (uvicorn api.app:app --port 8000)
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    css: false,
    pool: 'threads',
    poolOptions: {
      threads: { singleThread: true },
    },
    fileParallelism: false,
    testTimeout: 30000,
    exclude: ['node_modules/**', 'dist/**', 'e2e/**'],
  },
})
