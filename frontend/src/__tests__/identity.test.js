// identity.js — единая точка решения «какой student_id у этой карточки».
// Реальные имена не используются: фикстуры — синтетические «Персона N»,
// чтобы личность человека не зашивалась в код.
import { describe, expect, it } from 'vitest'
import {
  deriveIdentityKey,
  deriveStudentId,
  prefilledIdentity,
  resolveStudentId,
} from '../identity'

const persona = (tag) => `Персона ${tag}`

const card = (fields = {}) => ({
  fields: [
    { key: 'name', value: fields.name ?? '' },
    { key: 'learner_type', value: fields.type ?? '' },
    { key: 'grade', value: fields.grade ?? '' },
  ],
})

describe('deriveStudentId', () => {
  it('детерминирован: один и тот же ФИО+тип+класс → один и тот же id', () => {
    const a = deriveStudentId(persona('А'), 'student', '')
    const b = deriveStudentId(persona('А'), 'student', '')
    expect(a).toBe(b)
    expect(a).toMatch(/^stu_[0-9a-f]{8}$/)
  })

  it('тип меняет id (класс/студент)', () => {
    const s1 = deriveStudentId(persona('А'), 'student', '')
    const s2 = deriveStudentId(persona('А'), 'schoolchild', '7')
    expect(s1).not.toBe(s2)
  })

  it('разные люди → разные id', () => {
    const a = deriveStudentId(persona('А'), 'schoolchild', '7')
    const b = deriveStudentId(persona('Б'), 'student', '')
    expect(a).not.toBe(b)
  })
})

describe('resolveStudentId', () => {
  it('тот же человек (identity + канонический id совпадают) → сохраняет id', () => {
    const identity = deriveIdentityKey(persona('А'), 'student', '')
    const stored = { student_id: deriveStudentId(persona('А'), 'student', ''), identity }
    const r = resolveStudentId(persona('А'), 'student', '', stored, null)
    expect(r.studentId).toBe(stored.student_id)
    expect(r.legacy).toBe(false)
  })

  it('identity совпадает, но id — чужой (застрял с багованной версии) → пересоздаёт канонический id', () => {
    // браузер запомнил «Персона А/студент», но под произвольным историческим id
    const identity = deriveIdentityKey(persona('А'), 'student', '')
    const stored = { student_id: 'stu_oldlegacy', identity }
    const r = resolveStudentId(persona('А'), 'student', '', stored, null)
    expect(r.studentId).toBe(deriveStudentId(persona('А'), 'student', ''))
    expect(r.studentId).not.toBe('stu_oldlegacy')
  })

  it('другой человек при известной прошлой личности → новый детерминированный id', () => {
    const stored = { student_id: 'stu_old', identity: deriveIdentityKey(persona('А'), 'student', '') }
    const r = resolveStudentId(persona('Б'), 'schoolchild', '7', stored, null)
    expect(r.studentId).toBe(deriveStudentId(persona('Б'), 'schoolchild', '7'))
    expect(r.studentId).not.toBe('stu_old')
  })

  it('первое заполнение после апгрейда: тот же профиль → сохраняет исторический id и помечает legacy', () => {
    const stored = { student_id: 'stu_hist', student_name: persona('А') }
    const c = card({ name: persona('А'), type: 'schoolchild', grade: '7' })
    const r = resolveStudentId(persona('А'), 'schoolchild', '7', stored, c)
    expect(r.studentId).toBe('stu_hist')
    expect(r.legacy).toBe(true)
  })

  it('legacy-личность на следующих заходах сохраняет id (legacy: true)', () => {
    const identity = deriveIdentityKey(persona('А'), 'schoolchild', '7')
    const stored = { student_id: 'stu_hist', identity, legacy: true }
    const r = resolveStudentId(persona('А'), 'schoolchild', '7', stored, null)
    expect(r.studentId).toBe('stu_hist')
    expect(r.legacy).toBe(true)
  })

  it('первое заполнение после апгрейда, но несовпадение с профилем → новый id', () => {
    const stored = { student_id: 'stu_hist', student_name: persona('А') }
    const c = card({ name: persona('А'), type: 'schoolchild', grade: '7' })
    const r = resolveStudentId(persona('А'), 'student', '', stored, c)
    expect(r.studentId).toBe(deriveStudentId(persona('А'), 'student', ''))
    expect(r.studentId).not.toBe('stu_hist')
  })

  it('новый браузер (нет истории) → детерминированный id из identity', () => {
    const r = resolveStudentId(persona('В'), 'student', '', {}, null)
    expect(r.studentId).toBe(deriveStudentId(persona('В'), 'student', ''))
    expect(r.identity).toBe(deriveIdentityKey(persona('В'), 'student', ''))
  })

  it('случайный id нового браузера не прилипает (карточка пустого профиля)', () => {
    const stored = { student_id: 'stu_random', student_name: '' }
    const r = resolveStudentId(persona('В'), 'student', '', stored, card({}))
    expect(r.studentId).toBe(deriveStudentId(persona('В'), 'student', ''))
  })
})

describe('prefilledIdentity', () => {
  it('null на пустом профиле', () => {
    expect(prefilledIdentity(card({}))).toBeNull()
    expect(prefilledIdentity(null)).toBeNull()
    expect(prefilledIdentity({})).toBeNull()
  })

  it('строит identity из префилла профиля', () => {
    expect(prefilledIdentity(card({ name: persona('Б'), type: 'schoolchild', grade: '7' })))
      .toBe(deriveIdentityKey(persona('Б'), 'schoolchild', '7'))
  })
})