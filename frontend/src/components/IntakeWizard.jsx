// IntakeWizard — пошаговый чек-лист (раздел 9.2)
export default function IntakeWizard({ missing = [], question }) {
  const fields = ['learner_type', 'grade', 'subject', 'topic', 'has_textbook', 'chapter', 'mode']
  const labels = {
    learner_type: 'Тип обучаемого', grade: 'Класс', subject: 'Предмет',
    topic: 'Тема', has_textbook: 'Учебник', chapter: 'Глава', mode: 'Режим',
  }
  const remaining = new Set(missing)
  return (
    <div className="card intake">
      <h3>Чек-лист</h3>
      <div className="checklist">
        {fields.map((f) => (
          <span key={f} className={`check-item ${remaining.has(f) ? 'pending' : 'done'}`}>
            {labels[f]}
          </span>
        ))}
      </div>
      <div className="question-text">{question}</div>
    </div>
  )
}
