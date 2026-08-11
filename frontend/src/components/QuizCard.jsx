// QuizCard — карточка вопроса квиза (раздел 9.2)
export default function QuizCard({ q, onSelect }) {
  return (
    <div className="card quiz">
      <div className="quiz-meta">
        <span className="badge">{q.difficulty}</span>
        <span className="badge topic">{q.topic}</span>
        <span className="badge type">{q.answerType}</span>
      </div>
      <div className="question-text">{q.question}</div>
      {q.options && (
        <div className="options">
          {q.options.map((opt, i) => (
            <button key={i} className="option" onClick={() => onSelect(opt)}>
              {opt}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
