// KnowledgeGraphPanel — граф знаний учебника: SVG-визуализация + поиск и выбор темы (раздел 9.2)
import { useMemo, useState } from 'react'

function radialLayout(nodes) {
  const topics = nodes.filter((n) => n.type !== 'book')
  const book = nodes.find((n) => n.type === 'book')
  if (topics.length === 0) return { book: null, topics: [] }
  const cx = 160
  const cy = 150
  const r = 118
  const placed = topics.map((n, i) => {
    const angle = (2 * Math.PI * i) / topics.length - Math.PI / 2
    return {
      ...n,
      x: cx + r * Math.cos(angle),
      y: cy + r * Math.sin(angle),
      ring: 0,
    }
  })
  return { book: book ? { ...book, x: cx, y: cy } : null, topics: placed }
}

function GraphSvg({ nodes, activeTopic, onSelect }) {
  const { book, topics } = useMemo(() => radialLayout(nodes || []), [nodes])
  if (!book && topics.length === 0) return null
  return (
    <svg className="graph-svg" viewBox="0 0 320 300" role="img" aria-label="Граф тем учебника">
      {topics.map((t) => {
        const active = activeTopic === t.id
        return (
          <g key={t.id} className="kg-edge">
            <line x1={book?.x ?? 160} y1={book?.y ?? 150} x2={t.x} y2={t.y} stroke={t.color || '#888'} strokeOpacity="0.35" />
          </g>
        )
      })}
      {book && (
        <g
          className="kg-node kg-book"
          transform={`translate(${book.x},${book.y})`}
          onClick={() => onSelect && onSelect(book)}
        >
          <circle r="20" fill={book.color || '#69F0AE'} opacity="0.15" stroke={book.color || '#69F0AE'} strokeWidth="2" />
          <text textAnchor="middle" dominantBaseline="middle" className="kg-label" fontSize="9">📚</text>
          <title>{book.title}</title>
        </g>
      )}
      {topics.map((t) => (
        <g
          key={t.id}
          className={`kg-node ${activeTopic === t.id ? 'active' : ''}`}
          transform={`translate(${t.x},${t.y})`}
          onClick={() => onSelect && onSelect(t)}
        >
          <circle r={activeTopic === t.id ? 15 : 12} fill={t.color || '#69F0AE'} opacity="0.18" stroke={t.color || '#69F0AE'} strokeWidth="2" />
          <text textAnchor="middle" dominantBaseline="middle" className="kg-label" fontSize="9">
            {t.title.replace(/^Урок\s*(\d+).*/i, '$1')}
          </text>
          <title>{t.title}</title>
        </g>
      ))}
    </svg>
  )
}

export default function KnowledgeGraphPanel({ nodes = [], activeTopic = null, onSelect, busy = false }) {
  const [query, setQuery] = useState('')
  const topics = useMemo(() => (nodes || []).filter((n) => n.type !== 'book'), [nodes])
  const filtered = useMemo(() => {
    if (!query.trim()) return topics
    const q = query.trim().toLowerCase()
    return topics.filter((n) => n.title.toLowerCase().includes(q))
  }, [topics, query])

  if (!nodes || nodes.length === 0) return null

  return (
    <div className="card graph">
      <h3>Темы учебника · {topics.length}</h3>
      {activeTopic && (
        <div className="active-topic">
          Изучаем: <strong>{activeTopic}</strong>
        </div>
      )}
      <GraphSvg nodes={nodes} activeTopic={activeTopic} onSelect={onSelect} />
      <input
        className="topic-search"
        placeholder="Найти тему…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="topic-chips">
        {filtered.map((n) => (
          <button
            key={n.id}
            className={`topic-chip ${activeTopic === n.id ? 'active' : ''}`}
            style={{ '--chip-color': n.color || '#69F0AE' }}
            onClick={() => onSelect(n)}
            disabled={busy}
            title={n.type}
          >
            {n.title}
          </button>
        ))}
        {filtered.length === 0 && <div className="muted">Ничего не найдено</div>}
      </div>
      <div className="graph-legend">клик по теме → подготовка по ней</div>
    </div>
  )
}
