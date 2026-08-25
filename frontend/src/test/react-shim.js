// Shim: делаем React.act доступным для react-dom/test-utils
// В React 19 act экспортируется напрямую из 'react', а не из 'react-dom/test-utils'.
// @testing-library/react v16 внутренне использует require('react').act — патчим.
import * as React from 'react'

if (typeof globalThis.React === 'undefined') {
  globalThis.React = React
}

// Для CommonJS-совместимости react-dom/test-utils
Object.defineProperty(React, 'default', { value: React })

export default React
