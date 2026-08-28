import { readFileSync, writeFileSync } from 'fs'

const path = 'src/components/LessonPanel.jsx'
let content = readFileSync(path, 'utf8')

// Build replacement strings using char codes to avoid HTML entity corruption
const ampEntity = String.fromCharCode(38) + 'amp;'   // &
const ltEntity = String.fromCharCode(60) + 'lt;'      // <
const gtEntity = String.fromCharCode(62) + 'gt;'      // >

// Remove the old escapeHtml function and comment
const oldFunc = /\/\*\* Коды HTML-\sущностей для безопасного экранирования \*\/\n\n\/\*\* Преобразует.*?\*\/\n\/\*\* Экранирует.*?\nfunction escapeHtml\(str\) \{\n  return str\n    \.replace\(\/\\\&\/gu[^}]+\}/g

if (oldFunc.test(content)) {
  content = content.replace(oldFunc, '')
}

// Insert the new escapeHtml function BEFORE renderMarkdownLine
const newEscapeHtml = `/** Экранирует специальные HTML-символы */
function escapeHtml(str) {
  const A = ${JSON.stringify(ampEntity)}
  const L = ${JSON.stringify(ltEntity)}
  const G = ${JSON.stringify(gtEntity)}
  return str
    .replace(/&/gu, A)
    .replace(/</gu, L)
    .replace(/>/gu, G)
}

`

content = content.replace(
  /(\/\*\* Преобразует одну строку markdown в HTML[\s\S]*?)(function renderMarkdownLine)/,
  newEscapeHtml + '$2'
)

writeFileSync(path, content, 'utf8')
console.log('Fixed!')
