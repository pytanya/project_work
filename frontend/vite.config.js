import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { katexPlugin } from './vite-plugin-katex.js'

export default defineConfig({
  plugins: [react(), katexPlugin()],
  optimizeDeps: {
    include: ['katex'],
  },
  server: {
    proxy: {
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
    pool: 'forks',
    testTimeout: 30000,
    exclude: ['node_modules/**', 'dist/**', 'e2e/**'],
  },
})
