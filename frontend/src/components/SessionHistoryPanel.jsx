// SessionHistoryPanel — «История занятий» ученика (roadmap: per-student sessions).
// Лёгкая сводка закрытых сессий: дата, предмет · тема, режим, счёт квиза, был ли урок.
import { useEffect, useState } from 'react'

const MODE_LABELS = {
  lesson: 'Урок',
  quiz: 'Квиз',
  explain: 'Разбор',
  deep_dive: 'Глубокий разбор',
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

export default function SessionHistoryPanel({ studentId = '', reloadKey = 0 }) {
  const [sessions, setSessions] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(true)

  useEffect(() => {
    if (!studentId) return
    let cancelled = false
    setSessions(null)
    fetch(`/api/students/${encodeURIComponent(studentId)}/sessions`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body) => !cancelled && setSessions(body.sessions || []))
      .catch((e) => !cancelled && setError(String(e.message || e)))
    return () => { cancelled = true }
  }, [studentId, reloadKey])

  if (error) return null

  const list = sessions || []
  const empty = sessions !== null && list.length === 0

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
          {list.map((s, i) => (
            <div className="session-history__item" key={s.session_id || i}>
              <div className="session-history__row">
                <span className="session-history__date">{fmtDate(s.ts)}</span>
                <span className="session-history__mode">{MODE_LABELS[s.mode] || s.mode || ''}</span>
              </div>
              <div className="session-history__topic">
                {s.lesson_done && <span className="session-history__icon" title="Был урок">📚</span>}
                <span>{[s.subject, s.topic].filter(Boolean).join(' · ') || '—'}</span>
              </div>
              {(s.answered > 0) && (
                <div className="session-history__score">
                  <span className="session-history__correct">{s.correct}/{s.answered}</span>
                  <span className="session-history__score-label">верных ответов</span>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
