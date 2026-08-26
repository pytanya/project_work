// KnowledgeGraphPanel — «Созвездие» Canvas graph (roadmap #3 redesign):
// dark canvas, animated force-directed layout, constellation glow & twinkle,
// gradient edges, star dust, zoom/pan/drag/pinch, drill-down → wiki, chips.
import { useMemo, useState, useCallback, useRef, useEffect } from 'react'
import { createPortal } from 'react-dom'
import NoteItem from './NoteItem'

const EDGE_COLORS = {
  part_of: '#64DFDF',
  prerequisite: '#FFB703',
  related: '#B388FF',
}
const TYPE_LABELS = {
  book: 'Учебник',
  section: 'Раздел',
  page: 'Источник',
  topic: 'Тема',
  lesson: 'Урок',
  concept: 'Понятие',
  default: 'Тема',
}
const BG_COLOR = '#0d1117'
const LABEL_BG = 'rgba(13,17,23,0.88)'
const LABEL_COLOR = '#c9d1d9'

function masteryColor(mastery) {
  if (mastery === undefined || mastery === null) return null
  if (mastery >= 0.61) return '#4ade80'
  if (mastery >= 0.31) return '#fbbf24'
  return '#f87171'
}

/* Simple hash for stable per-node phase offset (twinkle) */
function hashId(id) {
  let h = 0
  const s = String(id)
  for (let i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0
  return (h & 0x7fffffff) / 0x7fffffff // 0..1
}

/* ------------------------------------------------------------------ */
/*  Star dust — fixed background particles (seeded, stable)           */
/* ------------------------------------------------------------------ */
function generateStarDust(W, H, count = 60) {
  const stars = []
  for (let i = 0; i < count; i++) {
    stars.push({
      x: ((i * 127 + 31) * 7919) % W,
      y: ((i * 311 + 97) * 6271) % H,
      r: 0.4 + ((i * 37) % 10) * 0.08,
      alpha: 0.02 + ((i * 53) % 10) * 0.005,
    })
  }
  return stars
}

/* ------------------------------------------------------------------ */
/*  Force simulation (runs per-frame via requestAnimationFrame)       */
/* ------------------------------------------------------------------ */
function createSim(rawNodes, rawEdges, W, H) {
  const nodes = rawNodes.map((n, i) => {
    const ang = (2 * Math.PI * i) / Math.max(1, rawNodes.length)
    const r = Math.min(W, H) * 0.28
    return {
      ...n,
      x: W / 2 + r * Math.cos(ang) + (Math.random() - 0.5) * 30,
      y: H / 2 + r * Math.sin(ang) + (Math.random() - 0.5) * 30,
      vx: 0, vy: 0, pinned: false,
      _phase: hashId(n.id), // stable twinkle phase
    }
  })
  const bookIdx = nodes.findIndex((n) => n.type === 'book')
  if (bookIdx >= 0) {
    nodes[bookIdx].x = W / 2
    nodes[bookIdx].y = H / 2
    nodes[bookIdx].pinned = true
  }
  const map = {}
  nodes.forEach((n) => { map[n.id] = n })
  const links = (rawEdges || []).filter((e) => map[e.source] && map[e.target])
  return { nodes, links, map, alpha: 1.0 }
}

function tickSim(sim, W, H) {
  if (sim.alpha < 0.002) return false
  const N = sim.nodes.length
  const k = Math.sqrt((W * H) / Math.max(1, N)) * 0.8

  // repulsion
  for (let i = 0; i < N; i++) {
    for (let j = i + 1; j < N; j++) {
      const a = sim.nodes[i], b = sim.nodes[j]
      let dx = a.x - b.x, dy = a.y - b.y
      const d = Math.sqrt(dx * dx + dy * dy) || 1
      const f = (k * k) / d * sim.alpha
      const fx = (dx / d) * f, fy = (dy / d) * f
      if (!a.pinned) { a.vx += fx; a.vy += fy }
      if (!b.pinned) { b.vx -= fx; b.vy -= fy }
    }
  }
  // attraction
  for (const e of sim.links) {
    const a = sim.map[e.source], b = sim.map[e.target]
    if (!a || !b) continue
    let dx = b.x - a.x, dy = b.y - a.y
    const d = Math.sqrt(dx * dx + dy * dy) || 1
    const f = (d * d) / k * sim.alpha * 0.25
    const fx = (dx / d) * f, fy = (dy / d) * f
    if (!a.pinned) { a.vx += fx; a.vy += fy }
    if (!b.pinned) { b.vx -= fx; b.vy -= fy }
  }
  // centering
  const cx = W / 2, cy = H / 2
  for (const n of sim.nodes) {
    if (n.pinned) continue
    n.vx += (cx - n.x) * 0.008 * sim.alpha
    n.vy += (cy - n.y) * 0.008 * sim.alpha
    n.vx *= 0.55; n.vy *= 0.55
    n.x += n.vx; n.y += n.vy
    n.x = Math.max(24, Math.min(W - 24, n.x))
    n.y = Math.max(24, Math.min(H - 24, n.y))
  }
  sim.alpha = Math.max(0, sim.alpha - 0.004)
  return true
}

/* ------------------------------------------------------------------ */
/*  Canvas drawing — «Созвездие» style                                */
/* ------------------------------------------------------------------ */
function drawGraph(ctx, sim, W, H, view, hovId, activeId, degree, nbrs, dpr, starDust) {
  const now = Date.now()
  ctx.save()
  ctx.scale(dpr, dpr)

  // background — deep space
  ctx.fillStyle = BG_COLOR
  ctx.fillRect(0, 0, W, H)

  // star dust — tiny fixed particles
  for (const s of starDust) {
    const twk = 0.5 + 0.5 * Math.sin(now * 0.0008 + s.x * 0.1)
    ctx.globalAlpha = s.alpha * twk
    ctx.fillStyle = '#fff'
    ctx.beginPath(); ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2); ctx.fill()
  }
  ctx.globalAlpha = 1

  // dot grid (subtle)
  ctx.fillStyle = 'rgba(255,255,255,0.018)'
  const gs = 24
  for (let x = ((view.x % gs) + gs) % gs; x < W; x += gs) {
    for (let y = ((view.y % gs) + gs) % gs; y < H; y += gs) {
      ctx.fillRect(x, y, 0.8, 0.8)
    }
  }

  ctx.save()
  ctx.translate(view.x, view.y)
  ctx.scale(view.scale, view.scale)

  const isConn = (id) => hovId && (nbrs[hovId]?.has(id) || id === hovId)
  const dim = (id) => hovId && !isConn(id)

  // edges — gradient trails
  for (const e of sim.links) {
    const s = sim.map[e.source], t = sim.map[e.target]
    if (!s || !t) continue
    const color = EDGE_COLORS[e.relation] || '#444'
    const lit = hovId && isConn(e.source) && isConn(e.target)
    const dd = dim(e.source) && dim(e.target)

    ctx.beginPath()
    ctx.moveTo(s.x, s.y); ctx.lineTo(t.x, t.y)

    // gradient edge — fades in the middle for constellation feel
    if (lit || !dd) {
      const grad = ctx.createLinearGradient(s.x, s.y, t.x, t.y)
      const a1 = lit ? 'bb' : '44'
      const a2 = lit ? '66' : '18'
      grad.addColorStop(0, color + a1)
      grad.addColorStop(0.5, color + a2)
      grad.addColorStop(1, color + a1)
      ctx.strokeStyle = grad
    } else {
      ctx.strokeStyle = color
    }
    ctx.globalAlpha = dd ? 0.04 : 1
    ctx.lineWidth = lit ? 1.8 : 0.7
    ctx.stroke()
  }
  ctx.globalAlpha = 1

  // nodes — constellation glow + twinkle
  for (const n of sim.nodes) {
    const r = n.type === 'book' ? 9 : 3.5 + Math.min((degree[n.id] || 0) * 0.8, 5)
    const mc = masteryColor(n.mastery)
    const baseColor = mc || n.color || '#69F0AE'
    const isAct = activeId === n.id
    const isHov = hovId === n.id
    const isDimmed = dim(n.id)

    // twinkle: soft pulsation unique per node
    const twinkle = isDimmed ? 0.12 : (0.72 + 0.28 * Math.sin(now * 0.0018 + n._phase * 6.28))

    ctx.globalAlpha = twinkle

    // outer halo glow (constellation feel) — all nodes get soft glow
    if (!isDimmed) {
      const glowR = r * (isHov || isAct ? 4.5 : 2.8)
      const grad = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, glowR)
      const glowAlpha = isHov || isAct ? '44' : '1a'
      grad.addColorStop(0, baseColor + glowAlpha)
      grad.addColorStop(0.5, baseColor + '0a')
      grad.addColorStop(1, 'transparent')
      ctx.beginPath(); ctx.arc(n.x, n.y, glowR, 0, Math.PI * 2)
      ctx.fillStyle = grad
      ctx.fill()
    }

    // focused glow ring (active / hovered)
    if (isHov || isAct) {
      ctx.save()
      ctx.shadowColor = baseColor
      ctx.shadowBlur = isAct ? 20 : 14
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 2, 0, Math.PI * 2)
      ctx.fillStyle = baseColor + '55'
      ctx.fill()
      ctx.restore()
    }

    // пульсирующее кольцо активной темы (5.1): ритмично расширяется/сжимается
    if (isAct && !isDimmed) {
      const pulse = 1 + 0.25 * Math.sin(now * 0.004 + n._phase * 6.28)
      const pr = (r + 4) * pulse
      ctx.beginPath(); ctx.arc(n.x, n.y, pr, 0, Math.PI * 2)
      ctx.strokeStyle = baseColor + 'aa'
      ctx.lineWidth = 1.2
      ctx.stroke()
    }

    // core node
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, Math.PI * 2)
    ctx.fillStyle = baseColor
    ctx.fill()
    if (isAct) {
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.8; ctx.stroke()
    }

    ctx.globalAlpha = 1

    // label (hover / active)
    if (isHov || isAct) {
      const title = String(n.title || '').slice(0, 36)
      ctx.font = '600 10px "JetBrains Mono","Golos Text",monospace'
      ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'
      const tw = ctx.measureText(title).width + 10
      ctx.fillStyle = LABEL_BG
      const lx = n.x - tw / 2, ly = n.y - r - 16
      ctx.beginPath(); ctx.roundRect(lx, ly, tw, 15, 4); ctx.fill()
      ctx.fillStyle = LABEL_COLOR
      ctx.fillText(title, n.x, n.y - r - 4)
    }

    // book emoji
    if (n.type === 'book') {
      ctx.font = '11px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'
      ctx.fillText('📚', n.x, n.y + 0.5)
    }
  }

  ctx.restore()
  ctx.restore()
}

/* roundRect polyfill for older browsers */
if (typeof CanvasRenderingContext2D !== 'undefined' && !CanvasRenderingContext2D.prototype.roundRect) {
  CanvasRenderingContext2D.prototype.roundRect = function (x, y, w, h, r) {
    if (typeof r === 'number') r = [r, r, r, r]
    const [tl, tr, br, bl] = r
    this.moveTo(x + tl, y)
    this.lineTo(x + w - tr, y); this.quadraticCurveTo(x + w, y, x + w, y + tr)
    this.lineTo(x + w, y + h - br); this.quadraticCurveTo(x + w, y + h, x + w - br, y + h)
    this.lineTo(x + bl, y + h); this.quadraticCurveTo(x, y + h, x, y + h - bl)
    this.lineTo(x, y + tl); this.quadraticCurveTo(x, y, x + tl, y)
    this.closePath()
    return this
  }
}

/* ------------------------------------------------------------------ */
/*  Component                                                         */
/* ------------------------------------------------------------------ */
export default function KnowledgeGraphPanel({ nodes = [], edges = [], activeTopic = null, onSelect, busy = false, sessionId = null }) {
  const canvasRef = useRef(null)
  const wrapRef = useRef(null)
  const simRef = useRef(null)
  const animRef = useRef(null)
  const viewRef = useRef({ x: 0, y: 0, scale: 1 })
  const dragRef = useRef(null)
  const hovIdRef = useRef(null)
  const starDustRef = useRef([])
  // Refs for draw state — avoid restarting animation loop on selection changes
  const activeIdRef = useRef(null)
  const degreeRef = useRef({})
  const nbrsRef = useRef({})
  // Touch pinch state
  const pinchRef = useRef(null)

  const [query, setQuery] = useState('')
  const [hovered, setHovered] = useState(null)
  const [tooltip, setTooltip] = useState(null)
  const [selected, setSelected] = useState(null)
  const [wiki, setWiki] = useState(null)
  const [related, setRelated] = useState(null)
  const [expanded, setExpanded] = useState(false)
  // Структурные узлы (разделы/уроки) скрыты по умолчанию — показываем понятийные (5.3)
  const [showStructural, setShowStructural] = useState(false)
  // Плавающее окно: позиция (null = по центру) + перетаскивание за заголовок
  const [floatPos, setFloatPos] = useState(null)
  const [dragging, setDragging] = useState(false)
  const floatRef = useRef(null)
  const floatDragRef = useRef(null)
  // Высота окна — для масштабируемого канваса в плавающем режиме
  const [winH, setWinH] = useState(() => (typeof window !== 'undefined' ? window.innerHeight : 700))

  useEffect(() => {
    const onR = () => setWinH(window.innerHeight)
    window.addEventListener('resize', onR)
    return () => window.removeEventListener('resize', onR)
  }, [])

  const topics = useMemo(() => {
    const all = (nodes || []).filter((n) => n.type !== 'book')
    if (showStructural) return all
    return all.filter((n) => n.type !== 'section' && n.type !== 'lesson')
  }, [nodes, showStructural])
  const nodeMap = useMemo(() => Object.fromEntries((nodes || []).map((n) => [n.id, n])), [nodes])
  const filtered = useMemo(() => {
    if (!query.trim()) return topics
    const q = query.trim().toLowerCase()
    return topics.filter((n) => String(n.title || '').toLowerCase().includes(q))
  }, [topics, query])

  // Группировка чипов: темы с parent_id (не «книга») — под заголовком родителя
  // (напр. подтемы веб-страницы под её доменом). Родитель с детьми становится
  // только заголовком группы (кликабельным), а не дублирующим чипом. При поиске — плоский список.
  const grouped = useMemo(() => {
    const childOf = new Set()
    for (const n of topics) {
      const pid = n.parent_id
      const parent = pid ? nodeMap[pid] : null
      if (parent && parent.type !== 'book') childOf.add(pid)
    }
    const top = []
    const byParent = {}
    for (const n of filtered) {
      const pid = n.parent_id
      const parent = pid ? nodeMap[pid] : null
      if (parent && parent.type !== 'book') {
        ;(byParent[pid] ||= []).push(n)
      } else if (!childOf.has(n.id)) {
        top.push(n)
      }
    }
    return { top, byParent }
  }, [filtered, topics, nodeMap])

  const degree = useMemo(() => {
    const m = {}
    for (const e of edges || []) {
      m[e.source] = (m[e.source] || 0) + 1
      m[e.target] = (m[e.target] || 0) + 1
    }
    degreeRef.current = m
    return m
  }, [edges])

  const neighbors = useMemo(() => {
    const m = {}
    for (const e of edges || []) {
      if (!m[e.source]) m[e.source] = new Set()
      if (!m[e.target]) m[e.target] = new Set()
      m[e.source].add(e.target)
      m[e.target].add(e.source)
    }
    nbrsRef.current = m
    return m
  }, [edges])

  // Keep activeId ref in sync without restarting animation
  useEffect(() => { activeIdRef.current = activeTopic || selected?.id || null }, [activeTopic, selected])

  /* canvas sizing — в плавающем окне масштабируется под высоту окна */
  const canvasH = expanded ? Math.max(420, Math.round(winH * 0.68)) : 260

  useEffect(() => {
    const c = canvasRef.current, w = wrapRef.current
    if (!c || !w) return
    const resize = () => {
      const dpr = window.devicePixelRatio || 1
      const cw = w.offsetWidth
      c.width = cw * dpr; c.height = canvasH * dpr
      c.style.width = cw + 'px'; c.style.height = canvasH + 'px'
      // Regenerate star dust on resize
      starDustRef.current = generateStarDust(cw, canvasH)
    }
    resize()
    window.addEventListener('resize', resize)
    return () => window.removeEventListener('resize', resize)
  }, [canvasH])

  /* simulation init + animation loop */
  useEffect(() => {
    if (!nodes || !nodes.length) return
    const c = canvasRef.current, w = wrapRef.current
    if (!c || !w) return
    const cw = w.offsetWidth
    simRef.current = createSim(nodes, edges, cw, canvasH)
    viewRef.current = { x: 0, y: 0, scale: 1 }
    starDustRef.current = generateStarDust(cw, canvasH)

    const loop = () => {
      const sim = simRef.current
      if (!sim) return
      const dpr = window.devicePixelRatio || 1
      const W = c.width / dpr, H = c.height / dpr
      tickSim(sim, W, H)
      const ctx = c.getContext('2d')
      // Read from refs so changes don't restart the loop
      drawGraph(ctx, sim, W, H, viewRef.current, hovIdRef.current,
        activeIdRef.current, degreeRef.current, nbrsRef.current, dpr,
        starDustRef.current)
      animRef.current = requestAnimationFrame(loop)
    }
    animRef.current = requestAnimationFrame(loop)
    return () => { if (animRef.current) cancelAnimationFrame(animRef.current) }
  }, [nodes, edges, canvasH])

  // При открытии плавающего окна — вместить граф целиком (масштаб под новую площадь)
  useEffect(() => {
    if (!expanded) return
    const t = setTimeout(() => fitToView(), 90)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded])

  // Перетаскивание плавающего окна за заголовок (мышь/тач через pointer events)
  const onFloatHeaderDown = useCallback((e) => {
    if (e.target.closest('button')) return  // не драгаем с кнопок (⊟ и др.)
    const el = floatRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    const base = floatPos || { x: r.left, y: r.top }
    floatDragRef.current = { sx: e.clientX, sy: e.clientY, ox: base.x, oy: base.y }
    setDragging(true)
  }, [floatPos])

  useEffect(() => {
    if (!dragging) return
    const move = (e) => {
      const d = floatDragRef.current
      if (d) setFloatPos({ x: d.ox + (e.clientX - d.sx), y: d.oy + (e.clientY - d.sy) })
    }
    const up = () => { floatDragRef.current = null; setDragging(false) }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
  }, [dragging])

  /* hit-test */
  const nodeAt = useCallback((cx, cy) => {
    const c = canvasRef.current; if (!c) return null
    const rect = c.getBoundingClientRect()
    const v = viewRef.current
    const mx = (cx - rect.left - v.x) / v.scale
    const my = (cy - rect.top - v.y) / v.scale
    const sim = simRef.current; if (!sim) return null
    for (let i = sim.nodes.length - 1; i >= 0; i--) {
      const n = sim.nodes[i]
      const r = n.type === 'book' ? 12 : 6 + Math.min((degree[n.id] || 0), 6)
      if ((mx - n.x) ** 2 + (my - n.y) ** 2 < r * r * 2.5) return n
    }
    return null
  }, [degree])

  const onMove = useCallback((e) => {
    const d = dragRef.current
    if (d) {
      const dx = e.clientX - d.sx, dy = e.clientY - d.sy
      if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
      viewRef.current = { ...viewRef.current, x: d.ox + dx, y: d.oy + dy }
      return
    }
    const n = nodeAt(e.clientX, e.clientY)
    hovIdRef.current = n?.id || null
    setHovered(n?.id || null)
    if (n) {
      const rect = wrapRef.current?.getBoundingClientRect()
      if (rect) {
        let left = e.clientX - rect.left + 14
        let top = e.clientY - rect.top - 12
        const ww = wrapRef.current?.offsetWidth || 300
        if (left + 200 > ww) left = e.clientX - rect.left - 210
        if (top < 4) top = e.clientY - rect.top + 18
        setTooltip({ node: n, left: Math.max(4, left), top: Math.max(4, top) })
      }
    } else { setTooltip(null) }
  }, [nodeAt])

  const onDown = useCallback((e) => {
    dragRef.current = { sx: e.clientX, sy: e.clientY, ox: viewRef.current.x, oy: viewRef.current.y, moved: false }
  }, [])

  const openNode = useCallback(async (node) => {
    setSelected(node); setWiki(null); setRelated(null)
    if (!node) return
    const sid = sessionId || sessionStorage.getItem('edututor_sid') || ''
    try {
      const [wRes, rRes] = await Promise.all([
        fetch(`/api/sessions/${sid}/graph/${encodeURIComponent(node.id)}/wiki`),
        fetch(`/api/sessions/${sid}/graph/${encodeURIComponent(node.id)}/related`),
      ])
      if (wRes.ok) { const b = await wRes.json(); setWiki(b.wiki) }
      if (rRes.ok) { const b = await rRes.json(); setRelated(b.related || []) }
    } catch (_) {}
  }, [sessionId])

  const onUp = useCallback((e) => {
    const d = dragRef.current; dragRef.current = null
    if (d && !d.moved) {
      const n = nodeAt(e.clientX, e.clientY)
      if (n) { openNode(n) }  // клик по узлу → карточка; изучение — кнопкой «Изучить»
    }
  }, [nodeAt, openNode])

  const onWheel = useCallback((e) => {
    e.preventDefault()
    const rect = canvasRef.current?.getBoundingClientRect(); if (!rect) return
    const v = viewRef.current
    const s = Math.min(3, Math.max(0.3, v.scale * (e.deltaY > 0 ? 0.91 : 1.09)))
    const r = 1 - s / v.scale
    viewRef.current = { x: v.x + (e.clientX - rect.left) * r, y: v.y + (e.clientY - rect.top) * r, scale: s }
  }, [])

  const onLeave = useCallback(() => {
    dragRef.current = null; hovIdRef.current = null; setHovered(null); setTooltip(null)
  }, [])

  /* --- Zoom controls --- */
  const applyZoom = useCallback((factor) => {
    const c = canvasRef.current; if (!c) return
    const v = viewRef.current
    const cx = c.offsetWidth / 2, cy = canvasH / 2
    const s = Math.min(3, Math.max(0.3, v.scale * factor))
    const r = 1 - s / v.scale
    viewRef.current = { x: v.x + cx * r, y: v.y + cy * r, scale: s }
  }, [canvasH])

  const zoomIn = useCallback(() => applyZoom(1.3), [applyZoom])
  const zoomOut = useCallback(() => applyZoom(0.75), [applyZoom])

  const fitToView = useCallback(() => {
    const sim = simRef.current; if (!sim || !sim.nodes.length) return
    const c = canvasRef.current; if (!c) return
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    for (const n of sim.nodes) {
      minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x)
      minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y)
    }
    const pad = 40
    const bw = (maxX - minX) + pad * 2, bh = (maxY - minY) + pad * 2
    if (bw < 1 || bh < 1) return
    const cw = c.offsetWidth
    const s = Math.min(cw / bw, canvasH / bh, 2.5)
    viewRef.current = {
      x: (cw - bw * s) / 2 - (minX - pad) * s,
      y: (canvasH - bh * s) / 2 - (minY - pad) * s,
      scale: s,
    }
  }, [canvasH])

  /* --- Touch: pinch-to-zoom --- */
  const onTouchStart = useCallback((e) => {
    if (e.touches.length === 2) {
      const t = e.touches
      const dist = Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY)
      const cx = (t[0].clientX + t[1].clientX) / 2
      const cy = (t[0].clientY + t[1].clientY) / 2
      pinchRef.current = { dist, cx, cy, scale: viewRef.current.scale, x: viewRef.current.x, y: viewRef.current.y }
    } else if (e.touches.length === 1) {
      const t = e.touches[0]
      dragRef.current = { sx: t.clientX, sy: t.clientY, ox: viewRef.current.x, oy: viewRef.current.y, moved: false }
    }
  }, [])

  const onTouchMove = useCallback((e) => {
    e.preventDefault()
    if (e.touches.length === 2 && pinchRef.current) {
      const t = e.touches
      const dist = Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY)
      const p = pinchRef.current
      const newScale = Math.min(3, Math.max(0.3, p.scale * (dist / p.dist)))
      const rect = canvasRef.current?.getBoundingClientRect()
      if (rect) {
        const cx = (t[0].clientX + t[1].clientX) / 2 - rect.left
        const cy = (t[0].clientY + t[1].clientY) / 2 - rect.top
        const r = 1 - newScale / p.scale
        viewRef.current = { x: p.x + cx * r, y: p.y + cy * r, scale: newScale }
      }
    } else if (e.touches.length === 1 && dragRef.current) {
      const t = e.touches[0]
      const d = dragRef.current
      const dx = t.clientX - d.sx, dy = t.clientY - d.sy
      if (Math.abs(dx) + Math.abs(dy) > 3) d.moved = true
      viewRef.current = { ...viewRef.current, x: d.ox + dx, y: d.oy + dy }
    }
  }, [])

  const onTouchEnd = useCallback((e) => {
    if (pinchRef.current) { pinchRef.current = null; return }
    const d = dragRef.current; dragRef.current = null
    if (d && !d.moved && e.changedTouches.length === 1) {
      const t = e.changedTouches[0]
      const n = nodeAt(t.clientX, t.clientY)
      if (n) { openNode(n) }
    }
  }, [nodeAt, openNode])

  useEffect(() => {
    const el = document.querySelector('.session-id')
    if (el) sessionStorage.setItem('edututor_sid', el.textContent.replace('сессия: ', '').trim())
  }, [])

  if (!nodes || nodes.length === 0) {
    return (
      <div className="card graph-panel">
        <div className="graph-panel__header"><h3>Граф знаний</h3></div>
        <div className="graph-panel-empty">Загрузите учебник или найдите источник — здесь появится карта темы.</div>
      </div>
    )
  }
  const selectedId = activeTopic || selected?.id
  const activeNode = (nodes || []).find((n) => n.id === activeTopic)

  const panel = (
    <div className={`card graph-panel ${expanded ? 'graph-panel--float' : ''}`}>
      <div className="graph-panel__header"
        onPointerDown={expanded ? onFloatHeaderDown : undefined}
        title={expanded ? 'Перетащите окно за заголовок' : undefined}>
        <h3>Граф знаний · {topics.length}</h3>
        <button className="graph-panel__toggle" onClick={() => setShowStructural((v) => !v)}
          title={showStructural ? 'Скрыть разделы/уроки (оставить понятия)' : 'Показать разделы и уроки'}>
          {showStructural ? '📚' : '🧩'}
        </button>
        <button className="graph-panel__expand" onClick={() => setExpanded((v) => !v)}
          title={expanded ? 'Свернуть' : 'Открыть в окне'}>
          {expanded ? '⊟' : '⛶'}
        </button>
      </div>
      {activeNode && (
        <div className="active-topic">Изучаем: <strong>{activeNode.title}</strong></div>
      )}
      <div ref={wrapRef} className="graph-canvas-wrap"
        onMouseMove={onMove} onMouseDown={onDown} onMouseUp={onUp}
        onMouseLeave={onLeave} onWheel={onWheel}
        onTouchStart={onTouchStart} onTouchMove={onTouchMove} onTouchEnd={onTouchEnd}>
        <canvas ref={canvasRef} className="graph-canvas" />
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
                {tooltip.node.attempts !== undefined && tooltip.node.attempts > 0 && (
                  <>
                    {' · '}Правильных: {tooltip.node.correct ?? 0}/{tooltip.node.attempts}
                    {' · '}точность {Math.round((tooltip.node.accuracy ?? 0) * 100)}%
                  </>
                )}
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
        <div className="graph-zoom-controls">
          <button onClick={zoomIn} title="Приблизить">+</button>
          <button onClick={zoomOut} title="Отдалить">−</button>
          <button onClick={fitToView} title="Вместить всё">⊡</button>
        </div>
        <div className="graph-zoom-hint">колесо / pinch — масштаб · drag — сдвиг</div>
      </div>
      {selected && (
        <div className="graph-wiki-card">
          <button className="graph-wiki-close" onClick={() => setSelected(null)}>✕</button>
          <div className="graph-wiki-title">{selected.title}</div>
          <button className="graph-wiki-study" onClick={() => onSelect && onSelect(selected)}
            disabled={busy}>
            📖 Изучить тему
          </button>
          {wiki && (
            <>
              {wiki.mastery !== undefined && (
                <div className={`wiki-mastery-line ${wiki.mastery >= 0.75 ? 'high' : wiki.mastery >= 0.45 ? 'mid' : 'low'}`}>
                  <div className="wiki-mastery-bar-bg">
                    <div className="wiki-mastery-bar-fill" style={{ width: `${Math.round(wiki.mastery * 100)}%` }} />
                  </div>
                  <span>{Math.round(wiki.mastery * 100)}% · попыток: {wiki.attempts} · точность: {Math.round((wiki.accuracy || 0) * 100)}%</span>
                </div>
              )}
              {wiki.notes && wiki.notes.length > 0 && (
                <div className="graph-wiki-notes">
                  {wiki.notes.map((n, i) => <NoteItem key={i} note={n} index={i} />)}
                </div>
              )}
              {wiki.concepts && wiki.concepts.length > 0 && (
                <div className="graph-wiki-concepts">
                  <div className="graph-wiki-concepts-title">Ключевые понятия</div>
                  {wiki.concepts.map((c, i) => <span key={i} className="graph-wiki-concept">{c}</span>)}
                </div>
              )}
              {wiki.body && <div className="graph-wiki-body">{wiki.body}</div>}
            </>
          )}
          {!wiki && <div className="muted">Статья ещё не создана — пройдите квиз по теме</div>}
          {related && related.length > 0 && (
            <div className="graph-wiki-related">
              <div className="graph-wiki-related-title">Связанные темы</div>
              {[...new Map(related.map((r) => [r.target, r])).values()]
                .filter((r) => r.target !== selected.id)
                .slice(0, 6)
                .map((r) => (
                  <button key={r.target}
                    className="graph-wiki-related-chip"
                    onClick={() => {
                      const pseudo = { id: r.target, title: r.target_title, type: r.target_type }
                      onSelect(pseudo); openNode(pseudo)
                    }}>
                    {r.target_title}
                  </button>
                ))}
            </div>
          )}
        </div>
      )}
      <input className="topic-search" placeholder="🔍 Найти тему…"
        value={query} onChange={(e) => setQuery(e.target.value)} />
      <div className="topic-chips">
        {(query.trim() ? filtered : grouped.top).map((n) => (
          <button key={n.id}
            className={`topic-chip ${selectedId === n.id ? 'active' : ''}`}
            style={{ '--chip-color': masteryColor(n.mastery) || n.color || '#69F0AE' }}
            onClick={() => openNode(n)}
            disabled={busy}
            title={`${n.title}${n.mastery !== undefined ? ` · мастерство ${Math.round(n.mastery * 100)}%` : ''}`}>
            {n.mastery !== undefined && (
              <span className="chip-mastery" style={{ background: masteryColor(n.mastery) }} />
            )}
            <span className="chip-text">{n.title}</span>
          </button>
        ))}
        {!query.trim() && Object.entries(grouped.byParent).map(([pid, children]) => (
          <div key={pid} className="topic-group">
            <button className={`topic-group-header ${selectedId === pid ? 'active' : ''}`}
              onClick={() => { const p = nodeMap[pid]; if (p) { onSelect(p); openNode(p) } }}
              disabled={busy}
              title={nodeMap[pid]?.title || pid}>
              <span className="topic-group-title">{nodeMap[pid]?.title || pid}</span>
              <span className="topic-group-count">{children.length}</span>
            </button>
            {children.map((n) => (
              <button key={n.id}
                className={`topic-chip ${selectedId === n.id ? 'active' : ''}`}
                style={{ '--chip-color': masteryColor(n.mastery) || n.color || '#69F0AE' }}
                onClick={() => openNode(n)}
                disabled={busy}
                title={`${n.title}${n.mastery !== undefined ? ` · мастерство ${Math.round(n.mastery * 100)}%` : ''}`}>
                {n.mastery !== undefined && (
                  <span className="chip-mastery" style={{ background: masteryColor(n.mastery) }} />
                )}
                <span className="chip-text">{n.title}</span>
              </button>
            ))}
          </div>
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
    </div>
  )

  if (expanded) {
    // Плавающее окно через портал: иначе position:fixed «прилипает» к сайдбару
    // (backdrop-filter создаёт containing-block) и граф не масштабируется.
    return createPortal(
      <div className="graph-float-backdrop" onClick={() => setExpanded(false)}>
        <div ref={floatRef} className="graph-float-sizer"
          style={{ left: floatPos ? `${floatPos.x}px` : '50%', top: floatPos ? `${floatPos.y}px` : '50%',
                   transform: floatPos ? 'none' : 'translate(-50%, -50%)' }}
          onClick={(e) => e.stopPropagation()}>
          {panel}
        </div>
      </div>,
      document.body,
    )
  }
  return panel
}
