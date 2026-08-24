/**
 * Парсер legacy-строк заметок из бэкенд-API.
 * 
 * Формат: "YYYY-MM-DD: feedback. Правильный ответ: ..."
 * Пример: "2026-08-19: Неверно. Правильный ответ: Уставные грамоты."
 * 
 * Извлекает: date, feedback, correct_answer
 * Также поддерживает вопросы типа "Вопрос: ... Ответ ученика: ... Правильный ответ: ..."
 * 
 * @param {string} noteStr — legacy-строка заметки
 * @returns {object|null} Структурированный объект или null если не распознан формат
 */
export function parseLegacyNote(noteStr) {
  if (typeof noteStr !== 'string' || !noteStr.trim()) return null

  const text = noteStr.trim()
  
  // Паттерн 1: "YYYY-MM-DD: feedback. Правильный ответ: answer."
  const datePattern = /^(\d{4}-\d{2}-\d{2})\s*:\s*(.+?)$/
  const dateMatch = text.match(datePattern)
  
  if (dateMatch) {
    const [, date, remainder] = dateMatch
    let feedback = ''
    let correctAnswer = ''
    let studentAnswer = ''
    let question = ''

    // Проверяем "Правильный ответ: ..."
    const correctPattern = /Правильный\s+ответ\s*:\s*(.+?)(?:\.\s*$)/
    const correctMatch = remainder.match(correctPattern)
    
    if (correctMatch) {
      correctAnswer = correctMatch[1].trim()
      // Всё до "Правильный ответ:" — это feedback (+ возможно student_answer)
      const beforeCorrect = remainder.replace(correctMatch[0], '').trim()
      
      // Проверяем "Вопрос: ... Ответ ученика: ... Правильный ответ: ..."
      const qPattern = /Вопрос\s*:\s*(.+?)\s+Ответ\s+ученика\s*:\s*(.+?)\s+Правильный/
      const qMatch = beforeCorrect.match(qPattern)
      
      if (qMatch) {
        question = qMatch[1].trim()
        studentAnswer = qMatch[2].trim()
        feedback = 'Замечание'
      } else {
        // Простой формат: "Неверно. Правильный ответ: ..."
        feedback = beforeCorrect.replace(/\.\s*$/, '') // убираем финальную точку если осталась
        if (!feedback) feedback = 'Замечание'
      }
    } else {
      // Просто дата + текст без "правильного ответа"
      feedback = remainder
    }

    return { date, feedback, question, student_answer: studentAnswer, correct_answer: correctAnswer }
  }

  // Паттерн 2: Без даты, но с "Правильный ответ: ..."
  const correctOnlyPattern = /Правильный\s+ответ\s*:\s*(.+?)(?:\.\s*$)/
  const coMatch = text.match(correctOnlyPattern)
  
  if (coMatch) {
    correctAnswer = coMatch[1].trim()
    const beforeCorrect = text.replace(coMatch[0], '').trim()
    
    let feedback = beforeCorrect || 'Замечание'
    let question = ''
    let studentAnswer = ''

    // Проверяем наличие вопроса
    const qPattern = /Вопрос\s*:\s*(.+?)\s+Ответ\s+ученика\s*:\s*(.+?)\s+Правильный/
    const qMatch = text.match(qPattern)
    
    if (qMatch) {
      question = qMatch[1].trim()
      studentAnswer = qMatch[2].trim()
      feedback = 'Замечание'
    }

    return { date: '', feedback, question, student_answer: studentAnswer, correct_answer: correctAnswer }
  }

  // Паттерн 3: Всё остальное — простой текст как feedback
  return { date: '', feedback: text, question: '', student_answer: '', correct_answer: '' }
}

/**
 * Проверка: является ли значение legacy-строкой
 * @param {any} note 
 * @returns {boolean}
 */
export function isLegacyNote(note) {
  return typeof note === 'string' && !!note.trim()
}

/**
 * Нормализация заметки к структурированному виду.
 * Если note уже объект — возвращает как есть.
 * Если note — строка — пытается распарсить.
 * 
 * @param {any} note 
 * @returns {object|null}
 */
export function normalizeNote(note) {
  // Уже структурированный объект (не массив)
  if (typeof note === 'object' && note !== null && !Array.isArray(note)) {
    return note
  }
  
  // Legacy-строка — парсим
  if (isLegacyNote(note)) {
    return parseLegacyNote(note)
  }
  
  return null
}
