import { readFileSync, writeFileSync } from 'fs'

const path = 'src/components/LessonPanel.jsx'
let content = readFileSync(path, 'utf8')

// Find and replace the HTMLEntities object line
// The problematic line contains corrupted entities that we need to remove entirely
const lines = content.split('\n')
const result = []
let skipNext = false

for (let i = 0; i < lines.length; i++) {
  const line = lines[i]
  
  // Skip the HTMLEntities object declaration line
  if (line.includes("HTMLEntities = Object.freeze")) {
    continue
  }
  
  // Skip the comment line before it
  if (i > 0 && lines[i-1].includes("Коды HTML-сущностей")) {
    continue
  }
  
  // Replace the three HTMLEntities.replace calls with escapeHtml call
  if (line.includes("HTMLEntities['")) {
    // This is the start of html = escapeHtml(html) block
    // Skip these 3 lines and add the single escapeHtml call
    skipNext = true
    continue
  }
  
  if (skipNext) {
    // Skip continuation lines (.replace patterns)
    if (line.trim().startsWith('.replace(')) {
      continue
    }
    skipNext = false
  }
  
  // Insert the escapeHtml function before renderMarkdownLine
  if (line.includes("function renderMarkdownLine(line)") && result.length > 0) {
    result.push('/** Экранирует специальные HTML-символы */')
    result.push('function escapeHtml(str) {')
    result.push('  return str')
    result.push("    .replace(/\\&/gu, String.fromCharCode(38) + 'amp;')")
    result.push("    .replace(/\\</gu, String.fromCharCode(60) + 'lt;')")
    result.push("    .replace(/\\>/gu, String.fromCharCode(62) + 'gt;')")
    result.push('}')
    result.push('')
  }
  
  result.push(line)
}

writeFileSync(path, result.join('\n'), 'utf8')
console.log('Fixed!')
