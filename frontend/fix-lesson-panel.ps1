$path = 'src/components/LessonPanel.jsx'
$content = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)

$lines = $content -split "`n"
$fixedLines = @()
for ($i = 0; $i -lt $lines.Count; $i++) {
    $line = $lines[$i]
    if ($line.Trim().StartsWith("html = html.replace(/&/g,")) {
        $fixedLines += "  html = html.replace(/&/g, '&')"
    } elseif ($line.Trim().StartsWith(".replace(/</g,")) {
        $fixedLines += "    .replace(/</g, '<')"
    } elseif ($line.Trim().StartsWith(".replace(/>/g,")) {
        $fixedLines += "    .replace(/>/g, '>')"
    } else {
        $fixedLines += $line
    }
}

$output = $fixedLines -join "`n"
[System.IO.File]::WriteAllText($path, $output, [System.Text.Encoding]::UTF8)
Write-Host "Fixed!"
