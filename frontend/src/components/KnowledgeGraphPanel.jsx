// KnowledgeGraphPanel — граф знаний в стиле Obsidian (roadmap #3):
// минимальные узлы-точки, размер по числу связей, подсветка соседей при наведении,
// данные — в тултипе, zoom/pan/drag, drill-down → wiki, поиск + чипсы тем.
import { useMemo, useState, useCallback, useRef, useEffect } from 'react'

const EDGE_COLORS = {
  part_of: '#64DFDF',
  prerequisite: '#FFB703',
  related: '#B388FF',
}
const EDGE_LABELS = {
  part_of: 'входит в',
  prerequisite: 'опирается на',
  related: 'связан',
}
const VIEW_W = 340
const VIEW_H = 320

const TYPE_LABELS = {
  book: 'Учебник',
  section: 'Раздел',
  page: 'Источник',
  topic: 'Тема',
  default: 'Тема',
}

function masteryColor(mastery) {
  if (mastery === undefined || mastery === null) return null
  if (mastery >= 0.75) return '#2E9E4F'   // зелёный — высокое
  if (mastery >= 0.45) return '#F0B429'   // жёлтый — среднее
  return '#E4572E'                        // красный — низкое
}

function radialLayout(nodes, edges) {
  const topics = (nodes || []).filter((n) => n.type !== 'book')
  const book = (nodes || []).find((n) => n.type === 'book')
  if (topics.length === 0) return { book: null, topics: [], byId: {} }
  const cx = VIEW_W / 2
  const cy = VIEW_H / 2
  const r = 118
  const placed = topics.map((n, i) => {
    const angle = (2 * Math.PI * i) / topics.length - Math.PI / 2
    return { ...n, x: cx + r * Math.cos(angle), y: cy + r * Math.sin(angle) }
  })
  const byId = {}
  if (book) byId[book.id] = { ...book, x: cx, y: cy }
  for (const t of placed) byId[t.id] = t
  return { book: book ? { ...book, x: cx, y: cy } : null, topics: placed, byId }
}

// Простая force-directed симуляция на чистом JS (~d3-force-lite) для больших графов:
// разводит перекрывающиеся узлы, сохраняя рёберные связи. Используется при >20 тем.
function forceLayout(nodes, edges, bookNode) {
  const W = VIEW_W
  const H = VIEW_H
  const pos = {}
  nodes.forEach((n, i) => {
    const ang = (2 * Math.PI * i) / Math.max(1, nodes.length)
    pos[n.id] = { x: W / 2 + 90 * Math.cos(ang), y: H / 2 + 90 * Math.sin(ang) }
  })
  if (bookNode) pos[bookNode.id] = { x: W / 2, y: H / 2 }

  const link = []
  for (const e of edges || []) {
    if (pos[e.source] && pos[e.target]) link.push({ a: e.source, b: e.target })
  }
  const k = Math.sqrt((W * H) / Math.max(1, nodes.length))

  // Итерации снижены с 260 до 120 (fix #3): для >20 узлов сходимость
  // достигается раньше, а синхронный расчёт в useMemo не «замораживает» UI.
  for (let iter = 0; iter < 120; iter++) {
    const forces = {}
    const ids = Object.keys(pos)
    for (const id of ids) forces[id] = { fx: 0, fy: 0 }
    // отталкивание
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const a = pos[ids[i]]
        const b = pos[ids[j]]
        let dx = a.x - b.x
        let dy = a.y - b.y
        const dist = Math.sqrt(dx * dx + dy * dy) || 1
        const f = (k * k) / dist
        const fx = (dx / dist) * f
        const fy = (dy / dist) * f
        forces[ids[i]].fx += fx
        forces[ids[i]].fy += fy
        forces[ids[j]].fx -= fx
        forces[ids[j]].fy -= fy
      }
    }
    // притяжение по рёбрам
    for (const l of link) {
      const a = pos[l.a]
      const b = pos[l.b]
      let dx = b.x - a.x
      let dy = b.y - a.y
      const dist = Math.sqrt(dx * dx + dy * dy) || 1
      const f = (dist * dist) / k
      const fx = (dx / dist) * f
      const fy = (dy / dist) * f
      forces[l.a].fx += fx
      forces[l.a].fy += fy
      forces[l.b].fx -= fx
      forces[l.b].fy -= fy
    }
    for (const id of ids) {
      const p = pos[id]
      p.x += forces[id].fx * 0.04 * 0.85
      p.y += forces[id].fy * 0.04 * 0.85
      p.x = Math.max(14, Math.min(W - 14, p.x))
      p.y = Math.max(14, Math.min(H - 14, p.y))
    }
  }

  const byId = {}
  for (const n of nodes) byId[n.id] = { ...n, ...pos[n.id] }
  if (bookNode) byId[bookNode.id] = { ...bookNode, ...pos[bookNode.id] }
  return {
    book: bookNode ? byId[bookNode.id] : null,
    topics: nodes.map((n) => byId[n.id]),
    byId,
  }
}

// Гибрид: radial для небольших графов (≤20 тем), force-directed для больших —
// radial складывает всё в круг и становится нечитаемым при 50+ узлах.
function computeLayout(nodes, edges) {
  const topics = (nodes || []).filter((n) => n.type !== 'book')
  const book = (nodes || []).find((n) => n.type === 'book')
  if (topics.length === 0) return { book: null, topics: [], byId: {} }
  if (topics.length <= 20) return radialLayout(nodes, edges)
  return forceLayout(topics, edges, book)
}

function shortTitle(title) {
  return String(title || '').replace(/^Урок\s*(\d+).*/i, '$1').slice(0, 14)
}

export default function KnowledgeGraphPanel({ nodes = [], edges = [], activeTopic = null, onSelect, busy = false }) {
  const [query, setQuery] = useState('')
  const [view, setView] = useState({ x: 0, y: 0, scale: 1 })
  const [selected, setSelected] = useState(null)
  const [wiki, setWiki] = useState(null)
  const [hovered, setHovered] = useState(null)      // id узла под курсором
  const [tooltip, setTooltip] = useState(null)      // {node, left, top}
  const dragRef = useRef(null)
  const svgRef = useRef(null)
  const wrapRef = useRef(null)

  const topics = useMemo(() => (nodes || []).filter((n) => n.type !== 'book'), [nodes])
  const filtered = useMemo(() => {
    if (!query.trim()) return topics
    const q = query.trim().toLowerCase()
    return topics.filter((n) => String(n.title || '').toLowerCase().includes(q))
  }, [topics, query])

  // Мемоизация layout (оптимизация #4): ключ = отсортированные id узлов + рёбра.
  // Изменение только атрибутов узлов (mastery и т.п.) не пересчитывает layout,
  // а hover/zoom/drag не ре-раннят его вовсе.
  const layoutKey = useMemo(
    () =>
      (nodes || []).map((n) => n.id).sort().join('|') +
      '#' +
      (edges || []).map((e) => `${e.source}->${e.target}`).sort().join('|'),
    [nodes, edges],
  )
  const layout = useMemo(() => computeLayout(nodes, edges), [layoutKey])

  // Степень узла (число связей) — размер точки в графе (как в Obsidian)
  const degree = useMemo(() => {
    const m = {}
    for (const e of edges || []) {
      m[e.source] = (m[e.source] || 0) + 1
      m[e.target] = (m[e.target] || 0) + 1
    }
    return m
  }, [edges])

  // Соседи по рёбрам — для подсветки при наведении
  const neighbors = useMemo(() => {
    const m = {}
    for (const e of edges || []) {
      if (!m[e.source]) m[e.source] = new Set()
      if (!m[e.target]) m[e.target] = new Set()
      m[e.source].add(e.target)
      m[e.target].add(e.source)
    }
    return m
  }, [edges])

  const nodeR = useCallback((id) => 5 + Math.min(degree[id] || 0, 7), [degree])

  const openNode = useCallback(async (node) => {
    setSelected(node)
    setWiki(null)
    if (!node) return
    try {
      const res = await fetch(`/api/sessions/${sessionStorage.getItem('edututor_sid') || ''}/graph/${encodeURIComponent(node.id)}/wiki`)
      if (res.ok) {
        const body = await res.json()
        setWiki(body.wiki)
      }
    } catch (_) {}
  }, [])

  // Позиция узла в пикселях обёртки (viewBox → CSS)
  const nodePx = useCallback(
    (node) => {
      const rect = svgRef.current?.getBoundingClientRect()
      if (!rect) return { x: 0, y: 0 }
      const sx = rect.width / VIEW_W
      return {
        x: (node.x + view.x) * view.scale * sx,
        y: (node.y + view.y) * view.scale * sx,
      }
    },
    [view],
  )

  const onNodeEnter = useCallback(
    (node) => {
      setHovered(node.id)
      const p = nodePx(node)
      const wrapW = wrapRef.current?.offsetWidth || 340
      let left = p.x + 16
      let top = p.y - 14
      if (left + 210 > wrapW) left = p.x - 220
      if (top < 6) top = p.y + 20
      setTooltip({ node, left: Math.max(4, left), top: Math.max(4, top) })
    },
    [nodePx],
  )

  const onNodeLeave = useCallback(() => {
    setHovered(null)
    setTooltip(null)
  }, [])

  // zoom/pan (те же жесты)
  const onWheel = useCallback((e) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const px = e.clientX - rect.left
    const py = e.clientY - rect.top
    setView((v) => {
      const scale = Math.min(2.5, Math.max(0.6, v.scale * (e.deltaY > 0 ? 0.9 : 1.1)))
      const ratio = 1 - scale / v.scale
      return { x: v.x + px * ratio, y: v.y + py * ratio, scale }
    })
  }, [])

  const onPanStart = useCallback((e) => {
    dragRef.current = { sx: e.clientX, sy: e.clientY, ox: view.x, oy: view.y, moved: false }
  }, [view])

  const onPanMove = useCallback((e) => {
    const d = dragRef.current
    if (!d) return
    const dx = e.clientX - d.sx
    const dy = e.clientY - d.sy
    if (Math.abs(dx) + Math.abs(dy) > 4) d.moved = true
    setView((v) => ({ ...v, x: d.ox + dx, y: d.oy + dy }))
  }, [])

  const onPanEnd = useCallback(() => {
    dragRef.current = null
  }, [])

  useEffect(() => {
    const el = document.querySelector('.session-id')
    if (el) sessionStorage.setItem('edututor_sid', el.textContent.replace('сессия: ', '').trim())
  }, [])

  if (!nodes || nodes.length === 0) return null

  const selectedId = activeTopic || selected?.id
  const connected = (id) => hovered && (neighbors[hovered]?.has(id) || id === hovered)
  const isDimmed = (id) => hovered && !connected(id)

  return (
    <div className="card graph">
      <h3>Темы учебника · {topics.length}</h3>
      {activeTopic && (
        <div className="active-topic">
          Изучаем: <strong>{activeTopic}</strong>
        </div>
      )}
      <div
        ref={wrapRef}
        className="graph-svg-wrap"
        onWheel={onWheel}
        onMouseDown={onPanStart}
        onMouseMove={onPanMove}
        onMouseUp={onPanEnd}
        onMouseLeave={onPanEnd}
      >
        <svg className="graph-svg" viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} role="img" aria-label="Граф тем учебника"
          ref={svgRef} style={{ cursor: dragRef.current ? 'grabbing' : 'grab' }}>
          <g transform={`translate(${view.x},${view.y}) scale(${view.scale})`}>
            {edges && edges.length > 0
              ? edges.map((ed, i) => {
                  const s = layout.byId[ed.source]
                  const t = layout.byId[ed.target]
                  if (!s || !t) return null
                  const color = EDGE_COLORS[ed.relation] || '#888'
                  const lit = hovered && connected(ed.source) && connected(ed.target)
                  return (
                    <line
                      key={`e${i}`}
                      x1={s.x} y1={s.y} x2={t.x} y2={t.y}
                      stroke={color}
                      strokeOpacity={lit ? 0.85 : isDimmed(ed.source) && isDimmed(ed.target) ? 0.05 : 0.35}
                      strokeWidth={lit ? 2 : 1.2}
                      className={`kg-edge ${isDimmed(ed.source) && isDimmed(ed.target) ? 'dim' : ''}`}
                    />
                  )
                })
              : topics.map((t) => {
                  const s = layout.book
                  return (
                    <line key={t.id} x1={s?.x ?? VIEW_W / 2} y1={s?.y ?? VIEW_H / 2} x2={t.x} y2={t.y}
                      stroke="#888" strokeOpacity={hovered ? (connected(t.id) ? 0.7 : 0.08) : 0.25} />
                  )
                })}
            {layout.book && (
              <g
                className={`kg-node kg-book ${isDimmed(layout.book.id) ? 'dim' : ''}`}
                transform={`translate(${layout.book.x},${layout.book.y})`}
                onClick={() => onSelect && onSelect(layout.book)}
                onMouseEnter={() => onNodeEnter(layout.book)}
                onMouseLeave={onNodeLeave}
              >
                <circle r={nodeR(layout.book.id) + 4} fill="#69F0AE" opacity="0.22" stroke="#69F0AE" strokeWidth="1.5" />
                <text textAnchor="middle" dominantBaseline="middle" fontSize="10">📚</text>
              </g>
            )}
            {topics.map((t) => {
              const active = selectedId === t.id
              const mc = masteryColor(t.mastery)
              const fill = mc || t.color || '#69F0AE'
              const r = nodeR(t.id)
              const dim = isDimmed(t.id)
              const showLabel = active || hovered === t.id
              return (
                <g
                  key={t.id}
                  className={`kg-node ${active ? 'active' : ''} ${dim ? 'dim' : ''}`}
                  transform={`translate(${t.x},${t.y})`}
                  onClick={(e) => {
                    e.stopPropagation()
                    if (dragRef.current && dragRef.current.moved) return
                    onSelect && onSelect(t)
                    openNode(t)
                  }}
                  onMouseEnter={() => onNodeEnter(t)}
                  onMouseLeave={onNodeLeave}
                >
                  <circle r={r} fill={fill} className={active ? 'kg-pulse' : ''} opacity={active ? 0.95 : 0.85} stroke={active ? '#fff' : fill} strokeWidth={active ? 2 : 1} />
                  {mc && <circle r="2.6" fill="#fff" opacity="0.95" cx={r + 3} cy={-(r + 3)} />}
                  {showLabel && (
                    <text textAnchor="middle" dy="-14" className="kg-hover-label">{shortTitle(t.title)}</text>
                  )}
                </g>
              )
            })}
          </g>
        </svg>
        {tooltip && (
          <div className="kg-tooltip" style={{ left: tooltip.left, top: tooltip.top }}>
            <div className="kg-tooltip-title">{tooltip.node.title}</div>
            <div className="kg-tooltip-meta">
              <span className="kg-tooltip-type">{TYPE_LABELS[tooltip.node.type] || 'Тема'}</span>
              {tooltip.node.section_number && <span>§{tooltip.node.section_number}</span>}
              <span>связей: {degree[tooltip.node.id] || 0}</span>
            </div>
            {tooltip.node.mastery !== undefined && tooltip.node.mastery !== null && (
              <div className="kg-tooltip-mastery">
                <i className="dot" style={{ background: masteryColor(tooltip.node.mastery) }} />
                мастерство {Math.round(tooltip.node.mastery * 100)}%
                {tooltip.node.attempts !== undefined ? ` · попыток: ${tooltip.node.attempts}` : ''}
              </div>
            )}
            {tooltip.node.source && <div className="kg-tooltip-source">{tooltip.node.source}</div>}
          </div>
        )}
        {busy && activeTopic && (
          <div className="graph-loading-overlay">
            <div className="graph-spinner" />
            <div className="graph-loading-text">Готовим материал…</div>
          </div>
        )}
        <div className="graph-zoom-hint">колесо — масштаб · drag — сдвиг</div>
      </div>
      {selected && (
        <div className="graph-wiki">
          <button className="graph-wiki-close" onClick={() => setSelected(null)}>✕</button>
          <div className="graph-wiki-title">{selected.title}</div>
          {wiki && (
            <>
              {wiki.mastery !== undefined && (
                <div className={`wiki-mastery-line ${wiki.mastery >= 0.75 ? 'high' : wiki.mastery >= 0.45 ? 'mid' : 'low'}`}>
                  Мастерство: {Math.round(wiki.mastery * 100)}% · попыток: {wiki.attempts} ·
                  точность: {Math.round((wiki.accuracy || 0) * 100)}%
                </div>
              )}
              {wiki.notes && wiki.notes.length > 0 && (
                <div className="graph-wiki-notes">
                  {wiki.notes.map((n, i) => (
                    <div key={i} className="graph-wiki-note">{n}</div>
                  ))}
                </div>
              )}
              {wiki.body && <div className="graph-wiki-body">{wiki.body}</div>}
            </>
          )}
          {!wiki && <div className="muted">Статья ещё не создана — пройдите квиз по теме</div>}
        </div>
      )}
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
            className={`topic-chip ${selectedId === n.id ? 'active' : ''}`}
            style={{ '--chip-color': masteryColor(n.mastery) || n.color || '#69F0AE' }}
            onClick={() => { onSelect(n); openNode(n) }}
            disabled={busy}
            title={`${n.title}${n.mastery !== undefined ? ` · мастерство ${Math.round(n.mastery * 100)}%` : ''}`}
          >
            {n.title}
          </button>
        ))}
        {filtered.length === 0 && <div className="muted">Ничего не найдено</div>}
      </div>
      <div className="graph-legend">
        <span><i style={{ background: EDGE_COLORS.part_of }} />входит в</span>
        <span><i style={{ background: EDGE_COLORS.prerequisite }} />опирается на</span>
        <span><i style={{ background: EDGE_COLORS.related }} />связан</span>
        <span><i className="dot dot-high" />высокое</span>
        <span><i className="dot dot-mid" />среднее</span>
        <span><i className="dot dot-low" />низкое</span>
      </div>
      <div className="graph-legend">наведите на точку → данные · клик → подготовка + статья</div>
    </div>
  )
}
