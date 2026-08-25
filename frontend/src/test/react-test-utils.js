// Shim для react-dom/test-utils — совместимость с React 19
// В React 19 act экспортируется напрямую из 'react', а не через свойство React.act.
// @testing-library/react v16 внутри использует require('react').act через react-dom/test-utils,
// поэтому мы проксируем вызовы к правильному act из 'react'.

import * as React from 'react'
const { act } = React

export { act }
export default React
