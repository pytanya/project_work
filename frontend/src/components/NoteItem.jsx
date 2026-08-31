// NoteItem — аккордеон-карточка структурированной заметки
import { useState } from 'react'
import { normalizeNote } from '../utils/parseNote'
import LatexText from './LatexText'

/**
 * Определение типа заметки по наличию полей
 * @param {object} note
 * @returns {'error' | 'clarification' | 'info'}
 */
function noteType(note) {
  const fb = (note.feedback || '').toLowerCase()
  if (fb.includes('ошибк') || fb.includes('неверн') || fb.includes('wrong') || fb.includes('incorrect')) return 'error'
  if (note.question || note.student_answer) return 'clarification'
  return 'info'
}

/**
 * Иконка для типа заметки
 */
function typeIcon(type) {
  switch (type) {
    case 'error': return '🔴'
    case 'clarification': return '🟡'
    default: return '🟢'
  }
}

export default function NoteItem({ note, index }) {
  const [open, setOpen] = useState(false)

  // Нормализация: парсим legacy-строки в объекты, объект оставляем как есть
  const parsed = normalizeNote(note)
  
  // Если не удалось распарсить — пропускаем
  if (!parsed) return null

  const { feedback, question, student_answer: studentAnswer, correct_answer: correctAnswer, date } = parsed
  
  // Не показываем пустые заметки
  if (!feedback && !question && !studentAnswer && !correctAnswer) return null

  const type = noteType(parsed)
  const icon = typeIcon(type)

  return (
    <div className={`topic-modal__note topic-modal__note--structured topic-modal__note--${type}`}>
      <button
        className="topic-modal__note-header"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        <span className="topic-modal__note-icon">{icon}</span>
        <span className="topic-modal__note-feedback"><LatexText text={feedback || 'Заметка'} /></span>
        {date && <span className="topic-modal__note-date">{date}</span>}
        <span className={`topic-modal__note-arrow ${open ? 'open' : ''}`}>▾</span>
      </button>

      {open && (
        <div className="topic-modal__note-body">
          {question && (
            <div className="topic-modal__note-question">
              <span className="topic-modal__note-label">Вопрос:</span>
              <span><LatexText text={question} /></span>
            </div>
          )}
          {studentAnswer && (
            <div className="topic-modal__note-student-answer">
              <span className="topic-modal__note-label">Ваш ответ:</span>
              <span>«<LatexText text={studentAnswer} />»</span>
            </div>
          )}
          {correctAnswer && (
            <div className="topic-modal__note-correct-answer">
              <span className="topic-modal__note-label">Правильный ответ:</span>
              <span><LatexText text={correctAnswer} /></span>
            </div>
          )}
          {/* Дополнительный комментарий — только если он не дублирует «Неверно»/правильный ответ */}
          {feedback && type !== 'error' && (
            <div className="topic-modal__note-feedback-text">
              <span className="topic-modal__note-label">{type === 'clarification' ? 'Заметка:' : 'Комментарий:'}</span>
              <span><LatexText text={feedback} /></span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
