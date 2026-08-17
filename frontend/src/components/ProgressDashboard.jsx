// ProgressDashboard — карта знаний обучаемого (раздел 9.2, Ж-6)
import { useEffect, useRef, useState } from 'react'

function barLevel(score) {
  if (score < 0.3) return 'low'
  if (score < 0.7) return 'mid'
  return 'high'
}

export default function ProgressDashboard({ knowledge = {}, correct = 0, total = 0 }) {
  const topics = Object.entries(knowledge)
  const accuracy = total > 0 ? Math.round((correct / total) * 100) : 0
  const [bumped, setBumped] = useState(false)
  const prevTotal = useRef(total)

  useEffect(() => {
    if (total > prevTotal.current) {
      setBumped(true)
      const t = setTimeout(() => setBumped(false), 500)
      prevTotal.current = total
      return () => clearTimeout(t)
    }
    prevTotal.current = total
  }, [total])

  return (
    <div className="card progress">
      <h3>Карта знаний</h3>
      {topics.length === 0 && <div className="muted">Пока нет данных</div>}
      {topics.map(([topic, score]) => {
        const pct = Math.round(score * 100)
        return (
          <div key={topic} className="topic-bar">
            <div className="topic-label">
              <span>{topic}</span>
              <span>{pct}%</span>
            </div>
            <div className="bar">
              <div className={`bar-fill ${barLevel(score)}`} style={{ width: `${pct}%` }} />
            </div>
          </div>
        )
      })}
      {total > 0 && (
        <div className="scoreline">
          Правильных: <span className={`score-value${bumped ? ' bump' : ''}`}>{correct}/{total}</span>
          {' '}
          <span className="muted">({accuracy}%)</span>
        </div>
      )}
    </div>
  )
}

