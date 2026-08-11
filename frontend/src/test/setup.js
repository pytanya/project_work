// Vitest setup: jest-dom матчеры + фейковый WebSocket (jsdom не реализует WS)
import '@testing-library/jest-dom/vitest'

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
