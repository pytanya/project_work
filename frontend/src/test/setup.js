// Vitest setup: jest-dom матчеры + фейковый WebSocket + React 19 act polyfill
import '@testing-library/jest-dom/vitest'
import { vi } from 'vitest'
import * as ReactActual from 'react'

// Polyfill: в React 19 `act` доступен как named export.
// react-dom/test-utils (CJS) делает require('react').act, что возвращает undefined.
// Патчим через vi.mock чтобы при импорте react свойство `act` существовало.
vi.mock('react', async () => {
  const actual = await vi.importActual('react')
  const mod = {}
  for (const key of Object.keys(actual)) {
    mod[key] = actual[key]
  }
  return mod
})

class FakeWebSocket {
  static instances = []
  constructor(url) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }
  close() {}
  send() {}
}

globalThis.WebSocket = FakeWebSocket
