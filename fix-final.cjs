const fs = require('fs')
const path = 'frontend/src/components/LessonPanel.jsx'
let content = fs.readFileSync(path, 'utf8')

// Define the replacement function using ONLY String.fromCharCode - NO literal &/</> anywhere
const replLines = [
  '/** Экранирует специальные HTML-символы */',
  'function escapeHtml(str) {',
  "  const A = String.fromCharCode(38) + 'amp;'".replace("'", "\x27"),
  "  const L = String.fromCharCode(38) + 'lt;'".replace("'", "\x27"),
  "  const G = String.fromCharCode(38) + 'gt;'".replace("'", "\x27"),
  '  return str',
    '.replace(/&/gu, A)',
    '.replace(/</gu, L)',
    '.replace(/>/gu, G)',
  '}',
].join('\n')

// Find the old problematic block: from "/** Экранирует" to end of escapeHtml function
const startPattern = /\/\*\* Экранирует специальные HTML-\u0441\u0438\u043c\u0432\u043e\u043b\u044b \*\/[\s\S]*?^}/m
const match = content.match(startPattern)

if (match && match.index !== undefined) {
  content = content.substring(0, match.index) + replLines + '\n' + content.substring(match.index + match[0].length)
  // Clean up double newlines
  content = content.replace(/\n{3,}/g, '\n\n')
}

fs.writeFileSync(path, content, 'utf8')
console.log('Fixed!')
