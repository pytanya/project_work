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
  // Лестница подсказок (бэкенд MAX_HINTS_PER_QUESTION=2): после двух уровней кнопка скрыта
  const hintCount = q.hints?.length || 0
  const stepCount = q.steps?.length || 0
  const hintExhausted = hintCount >= 2 || stepCount > 0
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
      {/* Подсказки уровней 1/2 — копятся в аккордеоне карточки (не в ленте чата) */}
      {q.hints?.length > 0 && (
        <div className="quiz-hintbox">
          {q.hints.map((h, i) => (
            <div className="quiz-hintbox__item" key={i}>
              <div className="quiz-hintbox__label">💡 Подсказка {h.level || i + 1}</div>
              <div className="quiz-hintbox__text"><LatexText text={h.text} /></div>
            </div>
          ))}
        </div>
      )}
      {/* Пошаговая декомпозиция «Шаг X/Y» — тоже внутри карточки */}
      {q.steps?.length > 0 && (
        <div className="quiz-stepbox">
          {q.steps.map((s, i) => (
            <div className="quiz-stepbox__item" key={i}>
              <div className="quiz-stepbox__label">Шаг {s.index || i + 1} из {s.total || q.steps.length}</div>
              <div className="quiz-stepbox__text"><LatexText text={s.text} /></div>
            </div>
          ))}
        </div>
      )}
      {onHint && !q.review && !hintExhausted && (
        <button className="btn btn-small quiz-hint-btn" onClick={() => onHint()}>
          {hintCount > 0 ? '💡 Ещё подсказка' : '💡 Подсказка'}
        </button>
      )}
    </div>
  )
}
