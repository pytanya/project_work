// Плагин для Vite: корректная обработка katex CSS и шрифтов
import { readFileSync, existsSync } from 'fs'
import { resolve, dirname, join } from 'path'

const KATEX_DIST = resolve(process.cwd(), 'node_modules/katex/dist')

export function katexPlugin() {
  return {
    name: 'vite-plugin-katex',
    enforce: 'pre',
    
    // Переписываем пути к шрифтам в CSS
    transform(code, id) {
      if (id.includes('katex') && id.endsWith('.css')) {
        // Заменяем относительные пути на абсолютные
        const katexCss = readFileSync(id, 'utf-8')
        const relativePath = dirname(id)
        
        // Заменяем font-url('/fonts/...') на font-url('/node_modules/katex/dist/fonts/...')
        const transformed = katexCss.replace(
          /url\(['"]?\/fonts\//g,
          `url('/node_modules/katex/dist/fonts/`
        )
        
        // Заменяем url('./fonts/...') на url('/node_modules/katex/dist/fonts/...')
        const transformed2 = transformed.replace(
          /url\(['"]?\.\.\/fonts\//g,
          `url('/node_modules/katex/dist/fonts/`
        )
        
        return {
          code: transformed2,
          map: null,
        }
      }
      return null
    },
  }
}
