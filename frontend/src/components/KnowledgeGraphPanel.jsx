// KnowledgeGraphPanel — граф знаний учебника: подготовка по темам (раздел 9.2)
export default function KnowledgeGraphPanel({ nodes = [], activeTopic = null, onSelect, busy = false }) {
  if (!nodes || nodes.length === 0) return null

  // верхнеуровневые темы (кроме корневого «учебника»)
  const topics = nodes.filter((n) => n.type !== 'book')

  return (
    <div className="card graph">
      <h3>Темы учебника</h3>
      {activeTopic && (
        <div className="active-topic">
          Изучаем: <strong>{activeTopic}</strong>
        </div>
      )}
      <div className="topic-chips">
        {topics.map((n) => (
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
      </div>
      <div className="graph-legend">клик по теме → подготовка по ней</div>
    </div>
  )
}
