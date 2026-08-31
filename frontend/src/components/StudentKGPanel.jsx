// StudentKGPanel — «Мои знания»: список тем ученика со статусами (roadmap #4).
// Показывает: освоенные / в процессе / не изученные + слабые места.
import { useEffect, useState, useMemo } from 'react'

const STATUS_COLORS = {
  not_studied: '#9ca3af',    // серый
  in_progress: '#fbbf24',    // жёлтый
  mastered: '#4ade80',       // зелёный
}
const STATUS_LABELS = {
  not_studied: 'Не изучалось',
  in_progress: 'В процессе',
  mastered: 'Освоено',
}

export default function StudentKGPanel({ studentId = '', subject = '' }) {
  const [kg, setKg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!studentId) return
    let cancelled = false
    setLoading(true)
    fetch(`/api/students/${encodeURIComponent(studentId)}/knowledge-graph?subject=${encodeURIComponent(subject)}`)
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) {
          setKg(data)
          setLoading(false)
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e.message || e))
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [studentId, subject])

  const stats = useMemo(() => {
    if (!kg || !kg.stats) return { total: 0, mastered: 0, in_progress: 0, not_studied: 0 }
    return kg.stats
  }, [kg])

  const topics = useMemo(() => {
    if (!kg || !kg.topics) return []
    return Object.values(kg.topics).sort((a, b) => {
      // Приоритет: in_progress > not_studied > mastered (по дате)
      const order = { in_progress: 0, not_studied: 1, mastered: 2 }
      const oa = order[a.status] ?? 1
      const ob = order[b.status] ?? 1
      if (oa !== ob) return oa - ob
      return (b.last_studied || '').localeCompare(a.last_studied || '')
    })
  }, [kg])

  if (!studentId) return null

  return (
    <div className="card student-kg-panel">
      <div className="student-kg-panel__header">
        <h3>Мои знания</h3>
        {subject && <span className="muted">· {subject}</span>}
      </div>

      {loading && <div className="muted" style={{ padding: '8px 12px' }}>Загрузка…</div>}
      {error && <div className="muted" style={{ color: 'var(--err)', padding: '8px 12px' }}>{error}</div>}

      {!loading && !error && (
        <>
          {/* Статистика */}
          <div className="student-kg-panel__stats">
            <div className="student-kg-stat">
              <span className="student-kg-stat-value">{stats.total}</span>
              <span className="student-kg-stat-label">всего</span>
            </div>
            <div className="student-kg-stat">
              <span className="student-kg-stat-value" style={{ color: STATUS_COLORS.mastered }}>{stats.mastered}</span>
              <span className="student-kg-stat-label">освоено</span>
            </div>
            <div className="student-kg-stat">
              <span className="student-kg-stat-value" style={{ color: STATUS_COLORS.in_progress }}>{stats.in_progress}</span>
              <span className="student-kg-stat-label">в процессе</span>
            </div>
            <div className="student-kg-stat">
              <span className="student-kg-stat-value" style={{ color: STATUS_COLORS.not_studied }}>{stats.not_studied}</span>
              <span className="student-kg-stat-label">не изучено</span>
            </div>
          </div>

          {/* Список тем */}
          <div className="student-kg-panel__list">
            {topics.length === 0 && (
              <div className="muted" style={{ padding: '8px 12px', fontSize: '12px' }}>
                Тем пока нет. Пройдите квиз по теме — знания накопятся.
              </div>
            )}
            {topics.map((topic) => (
              <div key={topic.topic_id} className="student-kg-item">
                <div className="student-kg-item__status" style={{ background: STATUS_COLORS[topic.status] }} />
                <div className="student-kg-item__content">
                  <div className="student-kg-item__title">{topic.title}</div>
                  <div className="student-kg-item__meta">
                    {topic.attempts > 0 && (
                      <span>
                        {topic.correct}/{topic.attempts} · {Math.round((topic.accuracy || 0) * 100)}%
                      </span>
                    )}
                    {topic.weak_areas && topic.weak_areas.length > 0 && (
                      <span className="muted"> · слабые: {topic.weak_areas.slice(0, 2).join(', ')}{topic.weak_areas.length > 2 ? '…' : ''}</span>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Легенда */}
          <div className="student-kg-panel__legend">
            <span><i style={{ background: STATUS_COLORS.mastered }} />освоено</span>
            <span><i style={{ background: STATUS_COLORS.in_progress }} />в процессе</span>
            <span><i style={{ background: STATUS_COLORS.not_studied }} />не изучено</span>
          </div>
        </>
      )}
    </div>
  )
}
