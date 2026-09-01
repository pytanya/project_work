// QuizCard — карточка вопроса квиза (раздел 9.2)
import { useState } from 'react'
import LatexText from './LatexText'

/** Компонент отображения отрывка с кнопкой раскрытия полного текста */
function ExcerptDisplay({ excerpt, fullExcerpt }) {
  const [expanded, setExpanded] = useState(false)

  if (!excerpt) return null

  // Если полный текст совпадает с превью — просто показываем
  if (!fullExcerpt || fullExcerpt === excerpt) {
    return (
      <div className="excerpt-card">
        <div className="excerpt-card__header">
          <span className="excerpt-card__icon">📜</span>
          <span className="excerpt-card__label">Отрывок текста</span>
        </div>
        <blockquote className="excerpt-card__text"><LatexText text={excerpt} /></blockquote>
      </div>
    )
  }

  // Если есть полный текст — показываем превью с кнопкой
  return (
    <div className="excerpt-card">
      <div className="excerpt-card__header">
        <span className="excerpt-card__icon">📜</span>
        <span className="excerpt-card__label">Отрывок текста</span>
        {!expanded && (
          <button
            className="excerpt-card__expand-btn"
            onClick={() => setExpanded(true)}
            title="Показать полный текст"
          >
            ▾
          </button>
        )}
      </div>
      <blockquote className="excerpt-card__text">
        <LatexText text={expanded ? fullExcerpt : excerpt} />
      </blockquote>
      {!expanded && fullExcerpt.length > excerpt.length && (
        <button
          className="excerpt-card__show-more"
          onClick={() => setExpanded(true)}
        >
          Показать полностью ({Math.round((fullExcerpt.length / 3) * 10) / 10} строк)
        </button>
      )}
    </div>
  )
}

export default function QuizCard({ q, onSelect, questionNum, totalQuestions, selectedOption, quickAnswer, correctCount, onHint = null }) {
  const progress = totalQuestions > 0 ? Math.min(100, Math.round((questionNum / totalQuestions) * 100)) : 0
  return (
    <div className="card quiz">
      {/* Отрывок — отображается перед вопросом */}
      {q.excerpt && <ExcerptDisplay excerpt={q.excerpt} fullExcerpt={q.excerpt} />}

      <div className="quiz-meta">
        {q.review && <span className="badge review">повторение</span>}
        <span className="badge">{q.difficulty}</span>
        <span className="badge topic">{q.topic}</span>
        <span className="badge type">{q.answerType}</span>
        {questionNum > 0 && totalQuestions > 0 && (
          <span className="badge counter">{q.review ? 'повторение' : 'вопрос'} {questionNum}/{totalQuestions}</span>
        )}
        {typeof correctCount === 'number' && (
          <span className="badge score">Правильных: {correctCount}</span>
        )}
      </div>
      {totalQuestions > 0 && (
        <div className="quiz-progress" title={`Вопрос ${questionNum}/${totalQuestions}`}>
          <div className="quiz-progress__fill" style={{ width: `${progress}%` }} />
        </div>
      )}
      <div className="question-text"><LatexText text={q.question} /></div>
      {q.options && (
        <div className="options">
          {q.options.map((opt, i) => (
            <button
              key={i}
              className={`option ${selectedOption === opt ? 'selected' : ''}`}
              onClick={() => onSelect(opt)}
            >
              <span className="option-letter">{String.fromCharCode(65 + i)}</span>
              <LatexText text={opt} />
            </button>
          ))}
        </div>
      )}
      {!quickAnswer && selectedOption && (
        <div className="quiz-hint">Нажмите «Подтвердить» или Enter для отправки</div>
      )}
      {onHint && !q.review && (
        <button className="btn btn-small quiz-hint-btn" onClick={() => onHint()}>
          💡 Подсказка
        </button>
      )}
    </div>
  )
}
