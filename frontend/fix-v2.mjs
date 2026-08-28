import { readFileSync, writeFileSync } from 'fs'

const path = 'src/components/LessonPanel.jsx'
let content = readFileSync(path, 'utf8')

// Find and replace everything from "/** Экранирует" to "function renderMarkdownLine"
const markerA = '/** Экранирует специальные HTML'
const markerB = '\n\nfunction renderMarkdownLine(line)'

const aIdx = content.indexOf(markerA)
const bIdx = content.indexOf(markerB, aIdx)

if (aIdx === -1 || bIdx === -1) {
  console.error('Markers not found')
  process.exit(1)
}

const before = content.substring(0, aIdx).trimEnd() + '\n\n'
const after = content.substring(bIdx)

// Build replacement using only char codes -- NO literal & / < / >
const NL = '\n'
const Q = String.fromCharCode(39) // single quote

// EscapeHtml function body
// const A = String.fromCharCode(38) + 'amp;'  --> this produces "&" at runtime
const lines = [
  '/** Экранирует специальные HTML-символы */',
  'function escapeHtml(str) {',
  `  const A = String.fromCharCode(38) + ${Q}amp;${Q}`,
  `  const L = String.fromCharCode(38) + ${Q}lt;${Q}`,
  `  const G = String.fromCharCode(38) + ${Q}gt;${Q}`,
  '  return str',
  `    .replace(/&/gu, A)`,
  `    .replace(/</gu, L)`,
  `    .replace(/>/gu, G)`,
  '}',
]

const replacement = lines.join(NL) + NL + NL

content = before + replacement + after

writeFileSync(path, content, 'utf8')
console.log('OK')
