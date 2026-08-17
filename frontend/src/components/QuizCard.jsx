// QuizCard — карточка вопроса квиза (раздел 9.2)
export default function QuizCard({ q, onSelect, questionNum, totalQuestions, selectedOption, quickAnswer }) {
  return (
    <div className="card quiz">
      <div className="quiz-meta">
        <span className="badge">{q.difficulty}</span>
        <span className="badge topic">{q.topic}</span>
        <span className="badge type">{q.answerType}</span>
        {questionNum > 0 && totalQuestions > 0 && (
          <span className="badge counter">вопрос {questionNum}/{totalQuestions}</span>
        )}
      </div>
      <div className="question-text">{q.question}</div>
      {q.options && (
        <div className="options">
          {q.options.map((opt, i) => (
            <button
              key={i}
              className={`option ${selectedOption === opt ? 'selected' : ''}`}
              onClick={() => onSelect(opt)}
            >
              <span className="option-letter">{String.fromCharCode(65 + i)}</span>
              {opt}
            </button>
          ))}
        </div>
      )}
      {!quickAnswer && selectedOption && (
        <div className="quiz-hint">Нажмите «Подтвердить» или Enter для отправки</div>
      )}
    </div>
  )
}

