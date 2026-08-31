// Vitest setup: jest-dom матчеры + фейковый WebSocket
import '@testing-library/jest-dom'

class FakeWebSocket {
  static instances = []
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3

  constructor(url) {
    this.url = url
    this.readyState = FakeWebSocket.OPEN
    FakeWebSocket.instances.push(this)
  }
  close() {
    this.readyState = FakeWebSocket.CLOSED
  }
  send() {}
}

beforeEach(() => {
  FakeWebSocket.instances = []
})

globalThis.WebSocket = FakeWebSocket
