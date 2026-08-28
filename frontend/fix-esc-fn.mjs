import { readFileSync, writeFileSync } from 'fs'

const path = 'src/components/LessonPanel.jsx'
let content = readFileSync(path, 'utf8')

// Build the new function body using ONLY char codes - no HTML entities anywhere
const SQ = "'" // Single quote = 39
const DQ = '"' // Double quote = 34
const NL = '\n' // newline = 10

// Create the complete new function text using charAt() and fromCharCode
// We'll insert it verbatim without any string interpolation of entities

const newFuncLines = [
  '/** Экранирует специальные HTML-символы */',
  'function escapeHtml(str) {',
]

// For const declarations, we build like: const A = String.fromCharCode(38) + 'amp;'
const AMP_EQ = String.fromCharCode(61)  // =
const PLUS_EQ = String.fromCharCode(43) + String.fromCharCode(61)  // +=
const PAREN_L = String.fromCharCode(40) // (
const PAREN_R = String.fromCharCode(41) // )
const DOT = String.fromCharCode(46)    // .
const COMMA = String.fromCharCode(44)  // ,
const COLON = String.fromCharCode(58)  // :
const SLASH = String.fromCharCode(47)  // /
const BACKSLASH = String.fromCharCode(92) // \

newFuncLines.push(`  const A = ${String.fromCharCode(38)}${DQ}${SQ}amp;${SQ}${DQ}`)
newFuncLines.push(`  const L = ${String.fromCharCode(38)}${DQ}${SQ}lt;${SQ}${DQ}`)
newFuncLines.push(`  const G = ${String.fromCharCode(38)}${DQ}${SQ}gt;${SQ}${DQ}`)

// Actually this still won't work because & gets decoded. Let me use pure fromCharCode:
newFuncLines[1] = `  const A = String.fromCharCode(38) + 'amp;'`
newFuncLines[2] = `  const L = String.fromCharCode(38) + 'lt;'`
newFuncLines[3] = `  const G = String.fromCharCode(38) + 'gt;'`

newFuncLines.push('  return str')
newFuncLines.push(`    .replace(/&/gu, A)`)
newFuncLines.push(`    .replace(/</gu, L)`)
newFuncLines.push(`    .replace(/>/gu, G)`)
newFuncLines.push('}')

const replacement = newFuncLines.join(NL) + NL + NL

// Find old block start and renderMarkdownLine position
const comment1Idx = content.indexOf('/* Коды HTML')
const comment2Idx = content.indexOf('/** Коды HTML-\u0441') // UTF-8 aware
const comment3Idx = content.indexOf('/** Коды HTML')

let startIdx = -1
for (const idx of [comment1Idx, comment2Idx, comment3Idx]) {
  if (idx > 0) { startIdx = idx; break }
}

// Also look for line starting with "/** Коды"
if (startIdx === -1) {
  const allLines = content.split(NL)
  for (let i = 0; i < allLines.length; i++) {
    if (allLines[i].includes('\u041a\u043e\u0434\u044b HTML')) {
      startIdx = content.indexOf(allLines[i])
      break
    }
  }
}

if (startIdx === -1) {
  // Try finding "Коды HTML" pattern
  const searchStr = '\u041a\u043e\u0434\u044b HTML-\u0441\u0443\u0449\u043d\u043e\u0441\u0442\u0435\u0439'
  startIdx = content.indexOf(searchStr)
  if (startIdx === -1) {
    startIdx = content.indexOf('HTML-\u0441\u0443\u0449\u043d\u043e\u0441\u0442\u0435\u0439')
  }
}

const renderMarker = 'function renderMarkdownLine(line)'
let endIdx = content.indexOf(renderMarker)

if (startIdx > 0 && endIdx > 0) {
  content = content.substring(0, startIdx).trimEnd() + NL + NL + replacement + NL + content.substring(endIdx)
  console.log(`Replaced lines from ${startIdx} to ${endIdx}`)
} else {
  console.error(`Could not find markers: start=${startIdx}, end=${endIdx}`)
}

writeFileSync(path, content, 'utf8')
console.log('Done!')
