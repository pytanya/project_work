// LessonDiagram — схема-иллюстрация к уроку (dual-coding).
// kind=flow: боксы и стрелки (этапы/причина→следствие)
// kind=cycle: расположение по кругу (круговорот)
// kind=map: географическая схема (узлы по координатам x,y 0..1, стрелки-течения warm/cold)

const W = 640
const H = 360

const EDGE_COLORS = {
  warm: '#c7453b',
  cold: '#2f6b8f',
  neutral: '#566069',
}

function wrap(text, maxChars) {
  const words = String(text || '').split(/\s+/).filter(Boolean)
  const lines = []
  let cur = ''
  for (const w of words) {
    const next = cur ? `${cur} ${w}` : w
    if (next.length > maxChars && cur) {
      lines.push(cur)
      cur = w
    } else {
      cur = next
    }
    if (lines.length >= 3) break
  }
  if (cur && lines.length < 3) lines.push(cur)
  return lines
}

function boxEdgePoint(cx, cy, tx, ty, w, h) {
  const dx = tx - cx
  const dy = ty - cy
  const len = Math.hypot(dx, dy) || 1
  const ux = dx / len
  const uy = dy / len
  const s = Math.min(w / 2 / Math.max(Math.abs(ux), 1e-6), h / 2 / Math.max(Math.abs(uy), 1e-6))
  return { x: tx - ux * s, y: ty - uy * s, ux, uy }
}

function flowLayout(n) {
  const cols = n <= 4 ? n : 3
  const rows = Math.ceil(n / cols)
  const bw = 128
  const bh = 64
  const gx = 64
  const gy = 46
  const totalW = cols * bw + (cols - 1) * gx
  const totalH = rows * bh + (rows - 1) * gy
  const ox = (W - totalW) / 2
  const oy = (H - totalH) / 2
  return Array.from({ length: n }, (_, i) => {
    let r = Math.floor(i / cols)
    let c = i % cols
    if (r % 2 === 1) c = cols - 1 - c
    return { x: ox + c * (bw + gx), y: oy + r * (bh + gy), w: bw, h: bh }
  })
}

function cycleLayout(n) {
  const cx = W / 2
  const cy = H / 2
  const radius = Math.min(215, 72 + 28 * n)
  const bw = 116
  const bh = 54
  return Array.from({ length: n }, (_, i) => {
    const a = (i / n) * Math.PI * 2 - Math.PI / 2
    return {
      x: cx + radius * Math.cos(a) - bw / 2,
      y: cy + radius * Math.sin(a) - bh / 2,
      w: bw,
      h: bh,
    }
  })
}

function mapLayout(nodes) {
  return nodes.map((n, i) => {
    const x = typeof n.x === 'number' && !Number.isNaN(n.x) ? Math.max(0, Math.min(1, n.x)) : null
    const y = typeof n.y === 'number' && !Number.isNaN(n.y) ? Math.max(0, Math.min(1, n.y)) : null
    if (x === null || y === null) {
      const col = i % 4
      const row = Math.floor(i / 4)
      return { x: 60 + col * 170, y: 80 + row * 120, w: 120, h: 46 }
    }
    return { x: x * W, y: y * H, w: 120, h: 46 }
  })
}

function NodeLabel({ lines, x, y, fontSize = 13, fill = '#21282e', bold = false }) {
  return (
    <text
      x={x}
      y={y}
      textAnchor="middle"
      fontSize={fontSize}
      fill={fill}
      fontWeight={bold ? 600 : 400}
    >
      {lines.map((l, i) => (
        <tspan key={i} x={x} dy={i === 0 ? 0 : 14}>
          {l}
        </tspan>
      ))}
    </text>
  )
}

function ArrowHead({ x, y, ux, uy, color, size = 9 }) {
  const px = -uy
  const py = ux
  const points = [
    `${x + ux * size},${y + uy * size}`,
    `${x - ux * size + px * size * 0.55},${y - uy * size + py * size * 0.55}`,
    `${x - ux * size - px * size * 0.55},${y - uy * size - py * size * 0.55}`,
  ].join(' ')
  return <polygon points={points} fill={color} stroke={color} strokeWidth="0.5" />
}

function FlowDiagram({ diagram }) {
  const nodes = diagram.nodes || []
  const positions = flowLayout(nodes.length)
  const byId = Object.fromEntries(nodes.map((n, i) => [n.id, { ...n, ...positions[i] }]))

  const edges = (diagram.edges || []).filter((e) => byId[e.source] && byId[e.target])
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={diagram.title || 'схема'} className="lesson-diagram">
      {edges.map((e, i) => {
        const s = byId[e.source]
        const t = byId[e.target]
        const sc = { x: s.x + s.w / 2, y: s.y + s.h / 2 }
        const tc = { x: t.x + t.w / 2, y: t.y + t.h / 2 }
        const ep = boxEdgePoint(sc.x, sc.y, tc.x, tc.y, t.w, t.h)
        const color = EDGE_COLORS[e.color] || EDGE_COLORS.neutral
        const mx = (sc.x + tc.x) / 2
        const my = (sc.y + tc.y) / 2 - 10
        return (
          <g key={i}>
            <line x1={sc.x} y1={sc.y} x2={ep.x} y2={ep.y} stroke={color} strokeWidth="2" />
            <ArrowHead x={ep.x} y={ep.y} ux={ep.ux} uy={ep.uy} color={color} />
            {e.label && (
              <text x={mx} y={my} textAnchor="middle" fontSize="11" fill={color}>
                {e.label}
              </text>
            )}
          </g>
        )
      })}
      {nodes.map((n) => {
        const p = byId[n.id]
        const lines = wrap(n.label, 16)
        return (
          <g key={n.id}>
            <rect
              x={p.x}
              y={p.y}
              width={p.w}
              height={p.h}
              rx="10"
              fill="#fcfdf9"
              stroke="#2f6b4f"
              strokeWidth="1.5"
            />
            <NodeLabel
              lines={lines}
              x={p.x + p.w / 2}
              y={p.y + p.h / 2 - ((lines.length - 1) * 14) / 2 + 4}
              bold
            />
          </g>
        )
      })}
    </svg>
  )
}

function CycleDiagram({ diagram }) {
  const nodes = diagram.nodes || []
  const positions = cycleLayout(nodes.length)
  const byId = Object.fromEntries(nodes.map((n, i) => [n.id, { ...n, ...positions[i] }]))
  const edges = (diagram.edges || [])
    .filter((e) => byId[e.source] && byId[e.target])
    .slice(0, nodes.length)

  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={diagram.title || 'цикл'} className="lesson-diagram">
      {edges.map((e, i) => {
        const s = byId[e.source]
        const t = byId[e.target]
        const sc = { x: s.x + s.w / 2, y: s.y + s.h / 2 }
        const tc = { x: t.x + t.w / 2, y: t.y + t.h / 2 }
        const ep = boxEdgePoint(sc.x, sc.y, tc.x, tc.y, t.w, t.h)
        const mx = (sc.x + ep.x) / 2
        const my = (sc.y + ep.y) / 2 - 12
        return (
          <g key={i}>
            <line x1={sc.x} y1={sc.y} x2={ep.x} y2={ep.y} stroke={EDGE_COLORS.neutral} strokeWidth="2" strokeDasharray={e.label ? '' : '5 4'} />
            <ArrowHead x={ep.x} y={ep.y} ux={ep.ux} uy={ep.uy} color={EDGE_COLORS.neutral} />
            {e.label && (
              <text x={mx} y={my} textAnchor="middle" fontSize="11" fill={EDGE_COLORS.neutral}>
                {e.label}
              </text>
            )}
          </g>
        )
      })}
      {nodes.map((n) => {
        const p = byId[n.id]
        const lines = wrap(n.label, 16)
        return (
          <g key={n.id}>
            <rect
              x={p.x}
              y={p.y}
              width={p.w}
              height={p.h}
              rx="22"
              fill="#e4efe7"
              stroke="#2f6b4f"
              strokeWidth="1.5"
            />
            <NodeLabel
              lines={lines}
              x={p.x + p.w / 2}
              y={p.y + p.h / 2 - ((lines.length - 1) * 14) / 2 + 4}
              bold
            />
          </g>
        )
      })}
    </svg>
  )
}

function MapDiagram({ diagram }) {
  const nodes = diagram.nodes || []
  const positions = mapLayout(nodes)
  const byId = Object.fromEntries(nodes.map((n, i) => [n.id, { ...n, ...positions[i] }]))
  const edges = (diagram.edges || []).filter((e) => byId[e.source] && byId[e.target])

  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={diagram.title || 'схема'} className="lesson-diagram">
      <rect x="0" y="0" width={W} height={H} rx="12" fill="#dfeef7" />
      <line x1="0" y1={H / 2} x2={W} y2={H / 2} stroke="#9cc3dc" strokeWidth="1" strokeDasharray="6 5" />

      {edges.map((e, i) => {
        const s = byId[e.source]
        const t = byId[e.target]
        const sc = { x: s.x, y: s.y }
        const tc = { x: t.x, y: t.y }
        const dx = tc.x - sc.x
        const dy = tc.y - sc.y
        const len = Math.hypot(dx, dy) || 1
        const ux = dx / len
        const uy = dy / len
        const mx = (sc.x + tc.x) / 2
        const my = (sc.y + tc.y) / 2
        const cx = mx - uy * 34
        const cy = my + ux * 34
        const color = EDGE_COLORS[e.color] || EDGE_COLORS.neutral
        const ep = { x: tc.x - ux * 16, y: tc.y - uy * 16 }
        return (
          <g key={i}>
            <path
              d={`M ${sc.x} ${sc.y} Q ${cx} ${cy} ${ep.x} ${ep.y}`}
              fill="none"
              stroke={color}
              strokeWidth="2.5"
              strokeLinecap="round"
            />
            <ArrowHead x={ep.x} y={ep.y} ux={ux} uy={uy} color={color} />
            {e.label && (
              <text x={mx} y={my - 8} textAnchor="middle" fontSize="11" fill={color} fontWeight="600">
                {e.label}
              </text>
            )}
          </g>
        )
      })}

      {nodes.map((n) => {
        const p = byId[n.id]
        const above = p.y > H - 46
        const ly = above ? p.y - 12 : p.y + 24
        return (
          <g key={n.id}>
            <circle cx={p.x} cy={p.y} r="7" fill="#2f6b4f" stroke="#fff" strokeWidth="2" />
            <text x={p.x} y={ly} textAnchor="middle" fontSize="12" fill="#21282e" fontWeight="600">
              {n.label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

export default function LessonDiagram({ diagram }) {
  if (!diagram || !Array.isArray(diagram.nodes) || diagram.nodes.length === 0) return null
  if (diagram.kind === 'cycle') return <CycleDiagram diagram={diagram} />
  if (diagram.kind === 'map') return <MapDiagram diagram={diagram} />
  return <FlowDiagram diagram={diagram} />
}
