// SessionHistoryPanel — «История занятий» ученика.
// Сводная статистика, группировка по предметам с прогресс-барами,
// фильтры (предмет / режим), подсветка слабых тем.
import { useEffect, useState, useMemo } from 'react'

const MODE_LABELS = {
  lesson: 'Урок',
  quiz: 'Квиз',
  explain: 'Разбор',
  deep_dive: 'Глубокий разбор',
}

const MODE_COLORS = {
  lesson: '#3b82f6',
  quiz: '#f59e0b',
  explain: '#8b5cf6',
  deep_dive: '#ec4899',
}

function fmtDate(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const today = new Date()
  const sameDay = d.toDateString() === today.toDateString()
  const time = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
  if (sameDay) return `сегодня, ${time}`
  const date = d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })
  return `${date}, ${time}`
}

function masteryPct(correct, answered) {
  if (!answered) return null
  return Math.round((correct / answered) * 100)
}

function masteryColor(pct) {
  if (pct === null) return 'var(--muted)'
  if (pct >= 75) return '#22c55e'
  if (pct >= 50) return '#f59e0b'
  return '#ef4444'
}

function computeStreak(sessions) {
  if (sessions.length === 0) return 0
  const dates = new Set()
  for (const s of sessions) {
    if (s.ts) dates.add(new Date(s.ts).toDateString())
  }
  let streak = 0
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  for (let i = 0; i < 365; i++) {
    const check = new Date(today)
    check.setDate(check.getDate() - i)
    if (dates.has(check.toDateString())) {
      streak++
    } else if (i > 0) {
      break
    }
  }
  return streak
}

export default function SessionHistoryPanel({ studentId = '', reloadKey = 0 }) {
  const [sessions, setSessions] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(false)
  const [filterSubject, setFilterSubject] = useState('')
  const [filterMode, setFilterMode] = useState('')

  useEffect(() => {
    if (!studentId) return
    let cancelled = false
    setSessions(null)
    const params = new URLSearchParams()
    if (filterSubject) params.set('subject', filterSubject)
    if (filterMode) params.set('mode', filterMode)
    const qs = params.toString()
    fetch(`/api/students/${encodeURIComponent(studentId)}/sessions${qs ? '?' + qs : ''}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body) => { if (!cancelled) setSessions(body.sessions || []) })
      .catch((e) => { if (!cancelled) setError(String(e.message || e)) })
    return () => { cancelled = true }
  }, [studentId, reloadKey, filterSubject, filterMode])

  const list = sessions || []
  const empty = sessions !== null && list.length === 0

  const allSubjects = useMemo(() => {
    const set = new Set()
    for (const s of list) {
      if (s.subject) set.add(s.subject)
    }
    return [...set].sort()
  }, [list])

  const allModes = useMemo(() => {
    const set = new Set()
    for (const s of list) {
      if (s.mode) set.add(s.mode)
    }
    return [...set].sort()
  }, [list])

  const quizzes = useMemo(() => list.filter((s) => s.mode === 'quiz' || s.mode === 'deep_dive'), [list])
  const totalAnswered = useMemo(() => quizzes.reduce((a, s) => a + (s.answered || 0), 0), [quizzes])
  const totalCorrect = useMemo(() => quizzes.reduce((a, s) => a + (s.correct || 0), 0), [quizzes])
  const avgPct = useMemo(() => totalAnswered > 0 ? Math.round((totalCorrect / totalAnswered) * 100) : null, [totalAnswered, totalCorrect])
  const streak = useMemo(() => computeStreak(list), [list])

  const grouped = useMemo(() => {
    const map = new Map()
    for (const s of list) {
      const subj = s.subject || 'Без предмета'
      if (!map.has(subj)) map.set(subj, [])
      map.get(subj).push(s)
    }
    return map
  }, [list])

  const subjectStats = useMemo(() => {
    const out = {}
    for (const [subj, items] of grouped) {
      const sq = items.filter((s) => s.mode === 'quiz' || s.mode === 'deep_dive')
      const ta = sq.reduce((a, s) => a + (s.answered || 0), 0)
      const tc = sq.reduce((a, s) => a + (s.correct || 0), 0)
      const pct = ta > 0 ? Math.round((tc / ta) * 100) : null
      out[subj] = { count: items.length, quizzes: sq.length, pct }
    }
    return out
  }, [grouped])

  const subjectEntries = useMemo(() => {
    return [...grouped.entries()].sort((a, b) => {
      const sa = subjectStats[a[0]]?.pct ?? 0
      const sb = subjectStats[b[0]]?.pct ?? 0
      return (sb ?? 0) - (sa ?? 0)
    })
  }, [grouped, subjectStats])

  if (error) return null

  return (
    <div className="card session-history">
      <button className="session-history__toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="session-history__title">История занятий</span>
        <span className="session-history__caret">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="session-history__body">
          {sessions === null && <div className="muted" style={{ fontSize: '12px' }}>Загружаем…</div>}
          {empty && (
            <div className="muted" style={{ fontSize: '12px', padding: '4px 2px' }}>
              Пока нет занятий — пройдите урок или квиз.
            </div>
          )}
          {!empty && (
            <>
              <div className="session-history__filters">
                <select
                  value={filterSubject}
                  onChange={(e) => setFilterSubject(e.target.value)}
                  className="session-history__select"
                >
                  <option value="">Все предметы</option>
                  {allSubjects.map((s) => (
                    <option key={s} value={s}>{s}</option>
                  ))}
                </select>
                <select
                  value={filterMode}
                  onChange={(e) => setFilterMode(e.target.value)}
                  className="session-history__select"
                >
                  <option value="">Все режимы</option>
                  {allModes.map((m) => (
                    <option key={m} value={m}>{MODE_LABELS[m] || m}</option>
                  ))}
                </select>
              </div>
              <div className="session-history__summary">
                <div className="session-history__stat">
                  <span className="session-history__stat-value">{list.length}</span>
                  <span className="session-history__stat-label">занятий</span>
                </div>
                {avgPct !== null && (
                  <div className="session-history__stat">
                    <span className="session-history__stat-value" style={{ color: masteryColor(avgPct) }}>
                      {avgPct}%
                    </span>
                    <span className="session-history__stat-label">средняя точность</span>
                  </div>
                )}
                {streak > 0 && (
                  <div className="session-history__stat">
                    <span className="session-history__stat-value">{streak}</span>
                    <span className="session-history__stat-label">дней подряд</span>
                  </div>
                )}
              </div>
              {subjectEntries.map(([subj, items]) => {
                const ss = subjectStats[subj]
                const barPct = ss?.pct ?? 0
                const barColor = masteryColor(barPct)
                return (
                  <div key={subj} className="session-history__group">
                    <div className="session-history__group-header">
                      <span className="session-history__subject">{subj}</span>
                      <span className="session-history__group-meta">
                        {ss?.quizzes > 0 && (
                          <>
                            <span>{ss.quizzes} квиз</span>
                            <span className="session-history__group-sep">·</span>
                          </>
                        )}
                        <span className="session-history__pct" style={{ color: barColor }}>
                          {ss?.pct !== null ? `${ss.pct}%` : '—'}
                        </span>
                      </span>
                    </div>
                    <div className="session-history__bar-track">
                      <div
                        className="session-history__bar-fill"
                        style={{ width: `${barPct}%`, backgroundColor: barColor }}
                      />
                    </div>
                    {items.map((s, i) => (
                      <div key={s.session_id || i} className="session-history__item">
                        <div className="session-history__row">
                          <span className="session-history__date">{fmtDate(s.ts)}</span>
                          <span
                            className="session-history__mode"
                            style={{
                              borderColor: (MODE_COLORS[s.mode] || '#999') + '40',
                              color: MODE_COLORS[s.mode] || '#333',
                              backgroundColor: (MODE_COLORS[s.mode] || '#999') + '12',
                            }}
                          >
                            {MODE_LABELS[s.mode] || s.mode || ''}
                          </span>
                        </div>
                        <div className="session-history__topic">
                          {s.lesson_done && <span className="session-history__icon" title="Был урок">📚</span>}
                          <span>{[s.subject, s.topic].filter(Boolean).join(' · ') || '—'}</span>
                        </div>
                        {s.answered > 0 && (
                          <div className="session-history__score">
                            <div className="session-history__mini-bar-track">
                              <div
                                className="session-history__mini-bar-fill"
                                style={{
                                  width: `${masteryPct(s.correct, s.answered)}%`,
                                  backgroundColor: masteryColor(masteryPct(s.correct, s.answered)),
                                }}
                              />
                            </div>
                            <span className="session-history__score-text">
                              {s.correct}/{s.answered} верных
                            </span>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )
              })}
            </>
          )}
        </div>
      )}
    </div>
  )
}
