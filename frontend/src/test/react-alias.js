// Aliased React shim for tests — добавляет .act для совместимости с react-dom/test-utils
import * as ReactOriginal from 'react'
const { act } = ReactOriginal

// Создаём объект с .act свойством для CommonJS-совместимости react-dom/test-utils
const ReactWithAct = new Proxy(ReactOriginal, {
  get(target, prop) {
    if (prop === 'act') return act
    return Reflect.get(target, prop)
  },
})

export default ReactWithAct
export * from 'react'
export { act }
