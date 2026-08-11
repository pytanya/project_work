// ProgressDashboard — карта знаний обучаемого (раздел 9.2, Ж-6)
export default function ProgressDashboard({ knowledge = {}, correct = 0, total = 0 }) {
  const topics = Object.entries(knowledge)
  return (
    <div className="card progress">
      <h3>Карта знаний</h3>
      {topics.length === 0 && <div className="muted">Пока нет данных</div>}
      {topics.map(([topic, score]) => (
        <div key={topic} className="topic-bar">
          <div className="topic-label">
            <span>{topic}</span>
            <span>{Math.round(score * 100)}%</span>
          </div>
          <div className="bar">
            <div className="bar-fill" style={{ width: `${Math.round(score * 100)}%` }} />
          </div>
        </div>
      ))}
      {total > 0 && <div className="scoreline">Правильных: {correct}/{total}</div>}
    </div>
  )
}
