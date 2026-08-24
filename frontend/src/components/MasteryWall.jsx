// MasteryWall — «Усвоение материала»: отдельный схлапываемый визуал.
// Тепловая карта тем по предметам (цвет = уровень мастерства), клик по ячейке
// раскрывает тему в браузере базы знаний ниже.
import { useState } from 'react'

function masteryClass(m) {
  if (m >= 0.75) return 'high'
  if (m >= 0.45) return 'mid'
  return 'low'
}

export default function MasteryWall({ subjects = [], onSelect }) {
  const [open, setOpen] = useState(true)
  const articles = subjects.flatMap((s) => (s.articles || []).map((a) => ({ ...a, subject: s.subject })))
  if (articles.length === 0) return null
  const mastered = articles.filter((a) => (a.mastery || 0) >= 0.75).length
  const pct = Math.round((mastered / articles.length) * 100)

  return (
    <div className="mastery-wall">
      <button className="mastery-wall__header" onClick={() => setOpen((v) => !v)}>
        <span className="mastery-wall__title">Усвоение</span>
        <span className="mastery-wall__meta">
          <span className="mastery-wall__ratio">{mastered}/{articles.length} · {pct}%</span>
          <span className="mastery-wall__arrow">{open ? '▾' : '▸'}</span>
        </span>
      </button>
      {open && (
        <div className="mastery-wall__grid">
          {subjects.map((s) => {
            const cells = (s.articles || []).filter((a) => a.topic)
            if (!cells.length) return null
            return (
              <div key={s.subject} className="mastery-wall__group">
                <div className="mastery-wall__subject">{s.subject}</div>
                <div className="mastery-wall__cells">
                  {cells.map((a) => (
                    <button key={a.topic}
                      className={`mastery-wall__cell ${masteryClass(a.mastery)}`}
                      title={`${a.topic} · ${Math.round((a.mastery || 0) * 100)}%`}
                      onClick={() => onSelect && onSelect(s.subject, a.topic)}>
                      {a.topic}
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
