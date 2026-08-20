// KnowledgeWikiPanel — «База знаний»: накопленные темы с мастерством между сессиями (roadmap #2)
import { useEffect, useState, useMemo } from 'react'

function masteryClass(m) {
  if (m >= 0.75) return 'high'
  if (m >= 0.45) return 'mid'
  return 'low'
}

function masteryLabel(m) {
  if (m >= 0.75) return 'высокое'
  if (m >= 0.45) return 'среднее'
  return 'низкое'
}

// --- цветовая палитра для тем ---
const PALETTE = [
  '#2f6b4f', // forest green
  '#c7453b', // teacher red
  '#e8922e', // amber
  '#4a90d9', // blue
  '#9b59b6', // purple
  '#1abc9c', // turquoise
  '#e67e22', // orange
  '#3498db', // sky
  '#e74c3c', // crimson
  '#2ecc71', // emerald
  '#f39c12', // yellow-orange
  '#8e44ad', // wisteria
]

function topicColor(index) {
  return PALETTE[index % PALETTE.length]
}

// --- вычисление SVG path для сектора DONUT chart (кольцевой сектор) ---
function describeDonutSegment(cx, cy, outerR, innerR, startAngle, endAngle) {
  const clampedEnd = Math.min(endAngle, startAngle + 359.99)
  
  // Внешняя дуга (по часовой стрелке от start к end)
  const outerStart = polarToCartesian(cx, cy, outerR, startAngle)
  const outerEnd = polarToCartesian(cx, cy, outerR, clampedEnd)
  
  // Внутренняя дуга (ПРОТИВ часовой стрелки от end к start, чтобы замкнуть путь)
  const innerEnd = polarToCartesian(cx, cy, innerR, clampedEnd)
  const innerStart = polarToCartesian(cx, cy, innerR, startAngle)
  
  const largeArcFlag = clampedEnd - startAngle <= 180 ? '0' : '1'
  
  return [
    'M', outerStart.x, outerStart.y,
    'A', outerR, outerR, 0, largeArcFlag, 1, outerEnd.x, outerEnd.y,
    'L', innerEnd.x, innerEnd.y,
    'A', innerR, innerR, 0, largeArcFlag, 0, innerStart.x, innerStart.y,
    'Z'
  ].join(' ')
}

// Переопределим describeArc для обратной совместимости
function describeArc(cx, cy, r, startAngle, endAngle) {
  return describeDonutSegment(cx, cy, r, 0, startAngle, endAngle)
}

function polarToCartesian(cx, cy, r, angleDeg) {
  // angles: 0 = top (12 o'clock), clockwise
  const rad = ((angleDeg - 90) * Math.PI) / 180
  return {
    x: cx + r * Math.cos(rad),
    y: cy + r * Math.sin(rad),
  }
}

// --- PieSlice с hover эффектом и tooltip ---
function PieSlice({ cx, cy, outerR, innerR, startAngle, endAngle, color, label, onMouseEnter, onMouseLeave, tooltipData }) {
  const midAngle = (startAngle + endAngle) / 2
  const pathD = describeDonutSegment(cx, cy, outerR, innerR, startAngle, endAngle)
  
  // Позиция метки: посередине между outer и inner радиусами
  const labelR = (outerR + innerR) / 2
  const labelPos = polarToCartesian(cx, cy, labelR, midAngle)
  
  return (
    <g
      className="wiki-slice"
      style={{ cursor: 'pointer' }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <path
        d={pathD}
        fill={color}
        opacity={0.75}
        stroke="var(--card)"
        strokeWidth="2"
        strokeLinejoin="round"
        className="wiki-slice-path"
      />
      {/* Метка: номер сектора */}
      {endAngle - startAngle > 20 && (
        <text
          x={labelPos.x}
          y={labelPos.y}
          textAnchor="middle"
          dominantBaseline="central"
          fontSize="10"
          fontWeight="700"
          fill="#fff"
          pointerEvents="none"
          style={{ textShadow: '0 1px 3px rgba(0,0,0,0.5)' }}
        >
          {label}
        </text>
      )}
      {/* SVG-native tooltip (браузерный title) */}
      <title>{tooltipData}</title>
    </g>
  )
}

// --- CircularDonutChart: кольцевая диаграмма с темами ---
function CircularDonutChart({ subjects }) {
  const [hoveredIndex, setHoveredIndex] = useState(null)
  
  // Flatten all articles across subjects with subject info
  const articlesWithSubject = useMemo(() => {
    const result = []
    subjects.forEach((s, si) => {
      (s.articles || []).forEach((a, ai) => {
        result.push({ ...a, subject: s.subject, subjectIndex: si, articleIndex: ai })
      })
    })
    return result
  }, [subjects])
  
  if (articlesWithSubject.length === 0) return null
  
  const totalAngle = 360
  let currentAngle = 0
  
  const cx = 150
  const cy = 150
  const outerR = 130
  const innerR = 65
  
  return (
    <div className="wiki-chart-wrapper">
      <svg
        className="wiki-chart-svg"
        viewBox={`0 0 300 300`}
        role="img"
        aria-label="Круговая диаграмма тем базы знаний"
      >
        {/* Центральный текст */}
        <circle cx={cx} cy={cy} r={innerR - 2} fill="var(--card)" stroke="var(--line)" strokeWidth="1" />
        <text x={cx} y={cy - 8} textAnchor="middle" fontSize="11" fontWeight="700" fill="var(--ink-soft)" fontFamily="var(--font-mono)">
          База знаний
        </text>
        <text x={cx} y={cy + 10} textAnchor="middle" fontSize="18" fontWeight="700" fill="var(--green-strong)" fontFamily="var(--font-mono)">
          {articlesWithSubject.length}
        </text>
        <text x={cx} y={cy + 26} textAnchor="middle" fontSize="9" fill="var(--muted)" fontFamily="var(--font-body)">
          тем изучено
        </text>
        
        {/* Сectors */}
        {articlesWithSubject.map((art, i) => {
          // Угловой размер proportional to mastery (higher mastery = bigger slice)
          // Minimum angle for visibility
          const angleSpan = Math.max(8, art.mastery * 180 + 15)
          const startAngle = currentAngle
          const endAngle = currentAngle + angleSpan
          currentAngle = endAngle
          
          const color = topicColor(i)
          
          // Tooltip content
          const tooltipLines = [
            `📚 ${art.title || art.topic}`,
            `📖 Предмет: ${art.subject}`,
            `⭐ Мастерство: ${Math.round((art.mastery || 0) * 100)}%`,
            `✓ Верных: ${art.correct ?? '—'} / ${art.attempts ?? '—'} (${Math.round((art.accuracy || 0) * 100)}%)`,
            art.last_studied ? `🕐 Обновлена: ${art.last_studied.slice(0, 10)}` : '',
            art.notes && art.notes.length > 0 ? `⚠️ Заметки: ${art.notes[0].replace(/^.*?:\s*/, '')}` : '',
          ].filter(Boolean).join('\n')
          
          return (
            <PieSlice
              key={`${art.topic}-${i}`}
              cx={cx}
              cy={cy}
              outerR={outerR}
              innerR={innerR}
              startAngle={startAngle}
              endAngle={endAngle}
              color={color}
              label={(i + 1).toString()}
              tooltipData={tooltipLines}
              onMouseEnter={() => setHoveredIndex(i)}
              onMouseLeave={() => setHoveredIndex(null)}
            />
          )
        })}
        
        {/* Legend: предметы */}
        <g transform="translate(10, 270)">
          {subjects.map((s, si) => (
            <g key={s.subject} transform={`translate(${si * 100}, 0)`}>
              <circle cx="0" cy="0" r="4" fill={topicColor(si)} />
              <text x="10" y="4" fontSize="9" fill="var(--ink-soft)" fontFamily="var(--font-body)">
                {s.subject} ({s.articles?.length || 0})
              </text>
            </g>
          ))}
        </g>
      </svg>
      
      {/* Hover detail panel */}
      {hoveredIndex !== null && (
        <WikiTooltip article={articlesWithSubject[hoveredIndex]} />
      )}
    </div>
  )
}

// --- Tooltip/Detail Panel для выбранной статьи ---
function WikiTooltip({ article }) {
  const pct = Math.round((article.mastery || 0) * 100)
  const accPct = Math.round((article.accuracy || 0) * 100)
  
  return (
    <div className="wiki-tooltip">
      <div className="wiki-tooltip-header">
        <span className="wiki-tooltip-title">{article.title || article.topic}</span>
        <span className={`wiki-tooltip-badge ${masteryClass(article.mastery)}`}>{masteryLabel(article.mastery)}</span>
      </div>
      <div className="wiki-tooltip-details">
        <div className="wiki-tooltip-row">
          <span className="wiki-tooltip-label">Предмет:</span>
          <span className="wiki-tooltip-value">{article.subject}</span>
        </div>
        <div className="wiki-tooltip-row">
          <span className="wiki-tooltip-label">Класс:</span>
          <span className="wiki-tooltip-value">{article.grade || '—'}</span>
        </div>
        <div className="wiki-tooltip-row">
          <span className="wiki-tooltip-label">Учебная программа:</span>
          <span className="wiki-tooltip-value wiki-tooltip-code">{article.curriculum || '—'}</span>
        </div>
        <div className="wiki-tooltip-row">
          <span className="wiki-tooltip-label">Мастерство:</span>
          <span className="wiki-tooltip-value wiki-tooltip-meter">
            <span className="wiki-tooltip-meter-fill" style={{ width: `${pct}%` }} />
            <span>{pct}%</span>
          </span>
        </div>
        <div className="wiki-tooltip-row">
          <span className="wiki-tooltip-label">Точность ответов:</span>
          <span className="wiki-tooltip-value">{accPct}% ({article.correct ?? 0}/{article.attempts ?? 0})</span>
        </div>
        {article.last_studied && (
          <div className="wiki-tooltip-row">
            <span className="wiki-tooltip-label">Последнее обновление:</span>
            <span className="wiki-tooltip-value">{article.last_studied.slice(0, 10)}</span>
          </div>
        )}
        {article.notes && article.notes.length > 0 && (
          <div className="wiki-tooltip-notes">
            <div className="wiki-tooltip-label">Заметки / пояснения:</div>
            {article.notes.map((note, ni) => (
              <div key={ni} className="wiki-tooltip-note">{note}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function KnowledgeWikiPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    fetch('/api/wiki')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body) => !cancelled && setData(body.subjects || []))
      .catch((e) => !cancelled && setError(String(e.message || e)))
    return () => {
      cancelled = true
    }
  }, [])

  if (error) return (
    <div className="card wiki">
      <h3>База знаний · ошибка</h3>
      <div className="muted" style={{ color: 'var(--err)' }}>Не удалось загрузить: {error}</div>
    </div>
  )
  
  const subjects = data || []
  const total = subjects.reduce((n, s) => n + (s.articles?.length || 0), 0)

  return (
    <div className="card wiki">
      <h3>База знаний · {total}</h3>
      {total === 0 && (
        <>
          <div className="muted">Знания накапливаются между сессиями</div>
          <div className="wiki-empty-hint">
            Пройдите квиз по теме, чтобы она появилась здесь
          </div>
        </>
      )}
      {total > 0 && (
        <CircularDonutChart subjects={subjects} />
      )}
    </div>
  )
}
