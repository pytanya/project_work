// LatexText — рендеринг LaTeX-формул через KaTeX (раздел 9.9)
import katex from 'katex'

export default function LatexText({ text }) {
  const textStr = String(text || '')
  
  // Если текст уже содержит katex HTML, не рендерим повторно
  if (textStr.includes('katex-html') || textStr.includes('katex-mathml') || textStr.includes('katex-display')) {
    return <span dangerouslySetInnerHTML={{ __html: textStr }} />
  }
  
  // Разделяем текст на LaTeX-блоки ($...$) и обычный текст
  // ВАЖНО: используем matchAll для безопасного парсинга (regex с 'g' сохраняет состояние)
  const latexRegex = /\$([^$]+)\$/g
  const parts = []
  let lastIndex = 0
  
  for (const m of textStr.matchAll(latexRegex)) {
    if (m.index > lastIndex) {
      parts.push({ type: 'text', content: textStr.slice(lastIndex, m.index) })
    }
    parts.push({ type: 'latex', content: m[1] })
    lastIndex = m.index + m[0].length
  }
  
  if (lastIndex < textStr.length) {
    parts.push({ type: 'text', content: textStr.slice(lastIndex) })
  }
  
  const hasLatex = parts.some(p => p.type === 'latex')
  
  if (!hasLatex) {
    return <span dangerouslySetInnerHTML={{ __html: textStr.replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>') }} />
  }
  
  return (
    <span>
      {parts.map((part, i) => {
        if (part.type === 'latex') {
          try {
            const html = katex.renderToString(part.content, {
              throwOnError: false,
              displayMode: false,
            })
            return <span key={i} dangerouslySetInnerHTML={{ __html: html }} />
          } catch (e) {
            return <span key={i}>${part.content}$</span>
          }
        }
        return <span key={i} dangerouslySetInnerHTML={{ __html: part.content.replace(/&/g, '&').replace(/</g, '<').replace(/>/g, '>') }} />
      })}
    </span>
  )
}
