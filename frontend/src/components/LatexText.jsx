// LatexText — рендеринг LaTeX-формул через KaTeX (раздел 9.9)
// Поддержка: $...$, $$...$$, \(...\), \[...\] + markdown-изображения ![alt](url)
import katex from 'katex'

/**
 * Regex tokeniser: разбивает текст на фрагменты:
 *  - display math: $$...$$ или \[...\]
 *  - inline math:  $...$ или \(...\)
 *  - markdown image: ![alt](url)
 *  - plain text: всё остальное
 */
function tokenize(text) {
  const tokens = []
  // Порядок важен: сначала display ($$), потом inline ($), потом images
  const regex = /\$\$([^$]+?)\$\$|\\\[(.+?)\\\]|\$([^$\n]+?)\$|\\\((.+?)\\\)|!\[([^\]]*)\]\(([^)]+)\)/gs
  let lastIndex = 0

  for (const m of text.matchAll(regex)) {
    if (m.index > lastIndex) {
      tokens.push({ type: 'text', content: text.slice(lastIndex, m.index) })
    }
    if (m[1] !== undefined) {
      tokens.push({ type: 'display-math', content: m[1] })
    } else if (m[2] !== undefined) {
      tokens.push({ type: 'display-math', content: m[2] })
    } else if (m[3] !== undefined) {
      tokens.push({ type: 'inline-math', content: m[3] })
    } else if (m[4] !== undefined) {
      tokens.push({ type: 'inline-math', content: m[4] })
    } else if (m[6] !== undefined) {
      tokens.push({ type: 'image', alt: m[5] || '', src: m[6] })
    }
    lastIndex = m.index + m[0].length
  }
  if (lastIndex < text.length) {
    tokens.push({ type: 'text', content: text.slice(lastIndex) })
  }
  return tokens
}

/** Безопасный escape HTML */
function escapeHtml(str) {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

export default function LatexText({ text }) {
  const textStr = String(text || '')

  // Если текст уже содержит katex HTML, не рендерим повторно
  if (textStr.includes('katex-html') || textStr.includes('katex-mathml') || textStr.includes('katex-display')) {
    return <span dangerouslySetInnerHTML={{ __html: textStr }} />
  }

  const tokens = tokenize(textStr)
  const hasSpecial = tokens.some(t => t.type !== 'text')

  if (!hasSpecial) {
    return <span dangerouslySetInnerHTML={{ __html: escapeHtml(textStr) }} />
  }

  return (
    <span>
      {tokens.map((tok, i) => {
        if (tok.type === 'display-math') {
          try {
            const html = katex.renderToString(tok.content, {
              throwOnError: false,
              displayMode: true,
              strict: 'ignore',
              trust: true,
            })
            return <span key={i} className="latex-display" dangerouslySetInnerHTML={{ __html: html }} />
          } catch {
            return <span key={i} className="latex-error">$${tok.content}$$</span>
          }
        }
        if (tok.type === 'inline-math') {
          try {
            const html = katex.renderToString(tok.content, {
              throwOnError: false,
              displayMode: false,
              strict: 'ignore',
              trust: true,
            })
            return <span key={i} dangerouslySetInnerHTML={{ __html: html }} />
          } catch {
            return <span key={i}>${tok.content}$</span>
          }
        }
        if (tok.type === 'image') {
          return (
            <span key={i} className="lesson-image-wrap">
              <img
                src={tok.src}
                alt={tok.alt}
                className="lesson-image"
                loading="lazy"
                onError={(e) => { e.target.style.display = 'none' }}
              />
              {tok.alt && <span className="lesson-image-caption">{tok.alt}</span>}
            </span>
          )
        }
        return <span key={i} dangerouslySetInnerHTML={{ __html: escapeHtml(tok.content) }} />
      })}
    </span>
  )
}
