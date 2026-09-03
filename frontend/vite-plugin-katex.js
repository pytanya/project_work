import { createHash } from 'crypto'

/**
 * Vite plugin that rewrites KaTeX CSS font url() paths to absolute paths
 * served from node_modules, ensuring fonts load correctly in both dev and build.
 */
export function katexPlugin() {
  const KATEX_FONT_RE = /url\((['"]?)fonts\//g

  return {
    name: 'vite-plugin-katex',
    enforce: 'pre',

    transform(code, id) {
      if (!id.includes('katex') || !id.endsWith('.css')) return null

      let result = code
      let count = 0

      result = result.replace(KATEX_FONT_RE, (match, quote) => {
        count++
        return `url(${quote}/node_modules/katex/dist/fonts/`
      })

      if (count === 0) return null

      return {
        code: result,
        map: null,
      }
    },
  }
}
