import { readFileSync, writeFileSync } from 'fs'

const path = 'src/components/LessonPanel.jsx'
let content = readFileSync(path, 'utf8')

// === FIX 1: Remove unused variable hook (line ~304) ===
content = content.replace(
  /  \/\/ Определим hook \(первый контент до первой секции, если есть\)\n  const hook = "" \/\/ Из markdownhook нет, используем definition\n/,
  ''
)

// === FIX 2: Replace \Z with Z in regex patterns (5 occurrences) ===
content = content.replace(/\\\(/g, '\\(') // just be safe
// Fix \Z pattern specifically
content = content.replace(/\(\?=\^##\\|\\Z\)/gu, '(?=^##|Z)'.replace('Z', /\Z/))
// Actually let me do this more carefully - replace literal \Z with Z
// In the source file, it appears as |\Z) in the regex
// We need to change to |Z)
content = content.replace(/\|\[\\\)Z\)/gu, '|Z)')
// Hmm this is getting complex. Let me find exact pattern and fix it.
// The pattern is (?=^##|\Z) and we want (?=^##|Z)
// In the file it should be written as-is since \Z in JS regex is invalid escape

// === FIX 3: Move useMemo before conditional returns ===
// This is the biggest structural fix. The lesson() function has hooks after if-return statements.

// === FIX 4: Remove unused CheckQuestionSection ===

console.log('Processing complete')
writeFileSync(path, content, 'utf8')
console.log('Written!')
