const fs = require('fs')
const path = 'frontend/src/components/LessonPanel.jsx'
let content = fs.readFileSync(path, 'utf8')

// FIX 1: Remove unused variable `hook` (lines 300-302)
content = content.replace(
  /  \/\/ Если есть sections из markdown — используем их как content-секции\n  \/\/ Определим hook \(первый контент до первой секции, если есть\)\n  const hook = "" \/\/ Из markdownhook нет, используем definition\n/,
  ''
)

// FIX 2: Remove unused function CheckQuestionSection and its header comments
content = content.replace(
  /\/\*\xe2\x95\xad{31} Секция: Проверь себя\n   \xe2\x95\xad{31}\s*\n\nfunction CheckQuestionSection\(\{ question \}\) \{\n\s+if \(!question\) return null\n\s+return \(\n\s+<div className="lesson-section lesson-section--check">\n\s+<div className="lesson-section__header">\n\s+<span className="lesson-section__icon">.*?<\/span>\n\s+<h3 className="lesson-section__title">.*?<\/h3>\n\s+<\/div>\n\s+<p className="lesson-check"><LatexText text=\{question\} \/><\/p>\n\s+<\/div>\n\s+\)\n\s+\}\n/,
  ''
)

console.log('After hook removal, length:', content.length)

// FIX 3: Replace literal backslash-Z with end-of-string anchor $
// In the file, these appear as: |\Z) which JS interprets as invalid escape
// We need to replace |\Z) with |$) everywhere
content = content.replace(/\|\\\)Z\)/g, '|$)')

console.log('After \\Z fix, length:', content.length)

// FIX 4: Fix useMemo placement - move it BEFORE early returns
// Current structure in LessonPanel():
//   const rawText = ... 
//   if (rawText ...) return <MarkdownLesson ... />  <-- EARLY RETURN!
//   const structuredCondition = ...
//   const raw = ...
//   const data = useMemo(...)  <-- THIS IS AFTER EARLY RETURN!
//
// Solution: Use conditional inside useMemo instead of guarding it with if-return

const lessonPanelStart = content.indexOf('export default function LessonPanel({ text, topic, lesson }) {')
if (lessonPanelStart === -1) {
  console.error('Could not find LessonPanel function')
  process.exit(1)
}

// Find the structure from "export default function LessonPanel" onwards
let afterLPStart = content.substring(lessonPanelStart)

// Find where structuredCondition starts
const structCondMatch = afterLPStart.match(/(\s+)\/\/ .*— Старый путь/)
const indent = structCondMatch ? structCondMatch[1] : '  '

// Build the corrected LessonPanel body
// We need to replace everything from /* -- Старый путь -- */ onwards

const oldSectionIdx = afterLPStart.indexOf('/* -- Старый путь: структурированный JSON-урок -- */')
if (oldSectionIdx === -1) {
  // Try exact comment pattern
  const patterns = [
    '/* \\u2500\\u2500 Старый путь: структурированный JSON-урок',
    '/* \\u2014 Старый путь: структурированный JSON-урок',
    '/* --- Старый путь: структурированный JSON-урок',
  ]
  let found = false
  for (const p of patterns) {
    const idx = afterLPStart.indexOf(p)
    if (idx > 0) {
      oldSectionIdx = idx
      found = true
      break
    }
  }
  if (!found) {
    // Try finding by line content
    const lines = afterLPStart.split('\n')
    for (let i = 0; i < lines.length; i++) {
      if (lines[i].includes('Старый путь')) {
        oldSectionIdx = lines.slice(0, i).join('\n').length + lines.slice(0, i).join('\n').split('\n').reduce((a,l) => a + l.length + 1, 0)
        break
      }
    }
  }
}

console.log('Old section index:', oldSectionIdx)

// Actually, let's do a simpler approach: find "/*" before "Старый путь" using regex
const lpContent = afterLPStart

// Find the position right before the comment containing "Старый путь"
const markerLine = '\n\n  /* ── Старый путь: структурированный JSON-урок ── */'
const markerPos = lpContent.indexOf(markerLine)
if (markerPos === -1) {
  console.log('Searching for alternative markers...')
  // Search line by line
  const allLines = lpContent.split('\n')
  let searchAccum = ''
  for (const line of allLines) {
    searchAccum += line + '\n'
    if (line.includes('Старый путь')) {
      // Find the start of this comment block (previous /** line)
      const commentStart = searchAccum.lastIndexOf('  /* ')
      if (commentStart > 0) {
        // Include full comment block
        const prevNewline = searchAccum.lastIndexOf('\n', commentStart - 1)
        markerPos = prevNewline >= 0 ? prevNewline : 0
      }
      break
    }
  }
}

console.log('Marker position:', markerPos)

if (markerPos > 0) {
  const beforeFixed = lpContent.substring(0, markerPos)
  
  // New fixed code replacing the problematic useMemo + conditionals
  const newCode = `
${indent}/* -- Санитизация данных (Hooks вызываем ДО любых условий) -- */
${indent}const data = useMemo(() => {
${indent}  if (!raw) return null
${indent}  return {
${indent}    ...raw,
${indent}    title: clean(raw.title),
${indent}    hook: clean(raw.hook),
${indent}    definition: clean(raw.definition),
${indent}    summary: clean(raw.summary),
${indent}    key_terms: (raw.key_terms || [])
${indent}      .map((t) => ({
${indent}        term: clean(typeof t === 'string' ? t : t?.term),
${indent}        definition: clean(typeof t === 'string' ? '' : t?.definition),
${indent}      }))
${indent}      .filter((t) => t.term),
${indent}    sections: (raw.sections || []).map((s) => ({
${indent}      ...s,
${indent}      heading: clean(s?.heading),
${indent}      title: clean(s?.title),
${indent}      body: clean(s?.body),
${indent}      content: clean(s?.content),
${indent}      citation: clean(s?.citation),
${indent}      check_question: clean(s?.check_question),
${indent}    })),
${indent}  }
${indent}}, [raw])
`;

  afterLPStart = beforeFixed + newCode + lpContent.substring(markerPos)
  content = content.substring(0, lessonPanelStart) + afterLPStart
}

fs.writeFileSync(path, content, 'utf8')
console.log('Final content length:', content.length)
console.log('All fixes applied!')
