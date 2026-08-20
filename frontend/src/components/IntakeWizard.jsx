// IntakeWizard — пошаговый чек-лист (раздел 9.2)
export default function IntakeWizard({ missing = [], question, fieldValues = {} }) {
  const fields = ['learner_type', 'grade', 'subject', 'topic', 'has_textbook', 'mode']
  const labels = {
    learner_type: 'Тип обучаемого', grade: 'Класс', subject: 'Предмет',
    topic: 'Тема', has_textbook: 'Учебник', mode: 'Режим',
  }
  // Человекочитаемые значения полей
  const humanize = (key, val) => {
    if (val == null || val === '') return null
    if (typeof val === 'boolean') return val ? 'Да' : 'Нет'
    // Специальные отображения для некоторых типов
    if (key === 'grade') return val.toString()
    if (key === 'mode') {
      const m = String(val).toLowerCase()
      if (m.includes('lesson')) return 'Урок'
      if (m.includes('quiz')) return 'Квиз'
      return String(val)
    }
    return String(val)
  }
  const remaining = new Set(missing)
  return (
    <div className="card intake">
      <h3>Чек-лист</h3>
      <div className="checklist">
        {fields.map((f) => {
          const done = !remaining.has(f)
          const rawVal = fieldValues[f]
          const displayVal = done ? humanize(f, rawVal) : null
          return (
            <span key={f} className={`check-item ${done ? 'done' : 'pending'}`} title={displayVal ? `${labels[f]}: ${displayVal}` : undefined}>
              {labels[f]}{displayVal && ` → ${displayVal}`}
            </span>
          )
        })}
      </div>
      <div className="question-text">{question}</div>
    </div>
  )
}
