// identity.js — личность ученика: детерминированная идентичность из ФИО+тип+класс.
//
// student_id выводится из неё: у разных людей разные ФИО/тип/класс → разные id →
// изолированные ветки данных (Wiki/история/мастерство). У одного и того же
// человека id воспроизводится детерминированно.

export function canonicalName(name) {
  return String(name || '').trim().replace(/\s+/g, ' ').toLowerCase()
}

export function deriveIdentityKey(name, type, grade) {
  return `${canonicalName(name)}|${String(type || '').trim().toLowerCase()}|${String(grade || '').trim().toLowerCase()}`
}

export function hashStr(s) {
  // FNV-1a 32-bit — стабильный детерминированный хеш (без обращения к crypto)
  let h = 0x811c9dc5
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i)
    h = Math.imul(h, 0x01000193)
  }
  return (h >>> 0).toString(16).padStart(8, '0')
}

export function deriveStudentId(name, type, grade) {
  return `stu_${hashStr(deriveIdentityKey(name, type, grade))}`
}

// Идентичность, которая уже живёт в профиле (префилл карточки знакомства).
// null — если профиль пуст (новый браузер, ещё никто не заполнял карточку).
export function prefilledIdentity(card) {
  if (!card?.fields) return null
  const get = (key) => {
    const f = card.fields.find((x) => x.key === key)
    return f ? String(f.value ?? '') : ''
  }
  const name = get('name')
  const type = get('learner_type')
  const grade = get('grade')
  if (!name && !type && !grade) return null
  return deriveIdentityKey(name, type, grade)
}

// Единственная точка принятия решения «какой student_id использовать».
//
// Правила:
// 1. Тот же человек, что в прошлый раз (identity совпадает) — сохраняем текущий
//    id (это может быть исторический id, под которым уже живут его данные).
// 2. Известная прошлая личность, но карточка заполнена ДРУГАЯ → другой человек:
//    детерминированный id из новой identity (новая изолированная ветка).
// 3. Первое заполнение (после апгрейда, legacy localStorage без identity) и филл
//    совпадает с идентичностью профиля → сохраняем исторический id (его данные).
// 4. Иначе — детерминированный id из заполненной identity.
export function resolveStudentId(name, type, grade, stored, card) {
  const identity = deriveIdentityKey(name, type, grade)
  const deterministic = deriveStudentId(name, type, grade)
  if (stored?.identity) {
    if (stored.identity === identity) return { studentId: stored.student_id, identity }
    return { studentId: deterministic, identity }
  }
  const legacy = prefilledIdentity(card)
  if (stored?.student_id && legacy === identity) {
    return { studentId: stored.student_id, identity }
  }
  return { studentId: deterministic, identity }
}