import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
  },
  webServer: [
    {
      // Бэкенд (FastAPI). health отвечает быстро (модель грузится при первой сессии)
      command: '.venv\\Scripts\\python.exe -m uvicorn api.app:app --port 8000',
      cwd: '../',
      url: 'http://127.0.0.1:8000/api/health',
      timeout: 120_000,
      reuseExistingServer: true,
    },
    {
      // Frontend (Vite, порт 5173)
      command: 'npm run dev',
      cwd: '.',
      url: 'http://localhost:5173',
      timeout: 60_000,
      reuseExistingServer: true,
    },
  ],
})
