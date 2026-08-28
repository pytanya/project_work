# Script to fix HTML entity references in LessonPanel.jsx
$path = 'src/components/LessonPanel.jsx'
$content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)

# Replace the HTMLEntities object + its usage with a simple escapeHtml function
$oldBlock = @'
/** Коды HTML-сущностей для безопасного экранирования */
const HTMLEntities = Object.freeze({ '&': '&', '<': '<', '>': '>', '"': '"', "'": '&#x27;' })

/** Преобразует одну строку markdown в HTML для dangerouslySetInnerHTML */
function renderMarkdownLine(line) {
  let html = String(line || '')
  
  // Экранируем HTML (порядок важен: & первым!)
  html = html.replace(/&/g, HTMLEntities['&'])
    .replace(/</g, HTMLEntities['<'])
    .replace(/>/g, HTMLEntities['>'])
'@

$newBlock = @'
/** Экранирует специальные HTML-симваты */
function escapeHtml(str) {
  return str
    .replace(/\&/gu, String.fromCharCode(38) + 'amp;')
    .replace(/\</gu, String.fromCharCode(60) + 'lt;')
    .replace(/\>/gu, String.fromCharCode(62) + 'gt;')
}

/** Преобразует одну строку markdown в HTML для dangerouslySetInnerHTML */
function renderMarkdownLine(line) {
  let html = String(line || '')
  
  // Экранируем HTML (порядок важен: & первым!)
  html = escapeHtml(html)
'@

$content = $content -replace [regex]::Escape($oldBlock), $newBlock
[System.IO.File]::WriteAllText($path, $content, [System.Text.Encoding]::UTF8)
Write-Host "Done!"
