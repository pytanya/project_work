// identity.js — единая точка решения «какой student_id у этой карточки».
import { describe, expect, it } from 'vitest'
import {
  deriveIdentityKey,
  deriveStudentId,
  prefilledIdentity,
  resolveStudentId,
} from '../identity'

const card = (fields = {}) => ({
  fields: [
    { key: 'name', value: fields.name ?? '' },
    { key: 'learner_type', value: fields.type ?? '' },
    { key: 'grade', value: fields.grade ?? '' },
  ],
})

describe('deriveStudentId', () => {
  it('детерминирован: тот же ФИО+тип+класс → тот же id', () => {
    const a = deriveStudentId('Татьяна Петрова', 'student', '')
    const b = deriveStudentId('татьяна    петрова', 'student', '')
    expect(a).toBe(b)
    expect(a).toMatch(/^stu_[0-9a-f]{8}$/)
  })

  it('разные личности → разные id (тип меняет id)', () => {
    const s1 = deriveStudentId('Татьяна Петрова', 'student', '')
    const s2 = deriveStudentId('Татьяна Петрова', 'schoolchild', '7')
    expect(s1).not.toBe(s2)
  })

  it('разные люди → разные id', () => {
    const a = deriveStudentId('Таня Иванова', 'schoolchild', '7')
    const b = deriveStudentId('Пётр Иванов', 'student', '')
    expect(a).not.toBe(b)
  })
})

describe('resolveStudentId', () => {
  it('тот же человек (identity совпадает) → сохраняет текущий id', () => {
    const stored = { student_id: 'stu_x', identity: deriveIdentityKey('Татьяна Петрова', 'student', '') }
    const r = resolveStudentId('Татьяна Петрова', 'student', '', stored, null)
    expect(r.studentId).toBe('stu_x')
  })

  it('другой человек при известной прошлой личности → новый детерминированный id', () => {
    // в браузере была Татьяна-студент ✓, а заполняют Таню-школьницу
    const stored = { student_id: 'stu_old', identity: deriveIdentityKey('Татьяна Петрова', 'student', '') }
    const r = resolveStudentId('Таня Иванова', 'schoolchild', '7', stored, null)
    expect(r.studentId).toBe(deriveStudentId('Таня Иванова', 'schoolchild', '7'))
    expect(r.studentId).not.toBe('stu_old')
  })

  it('первое заполнение после апгрейда: тот же профиль → сохраняет исторический id (данные живы)', () => {
    // localStorage старого формата: только id+имя, identity ещё нет; профиль на бэке школьница 7 «Татьяна»
    const stored = { student_id: 'stu_tanya', student_name: 'Татьяна' }
    const c = card({ name: 'Татьяна', type: 'schoolchild', grade: '7' })
    const r = resolveStudentId('Татьяна', 'schoolchild', '7', stored, c)
    expect(r.studentId).toBe('stu_tanya')
  })

  it('первое заполнение после апгрейда, но другой человек (ИЛИ несовпадение с профилем) → новый id', () => {
    // профиль был «Татьяна/школьница/7», а заполняют студентку (тот же браузер)
    const stored = { student_id: 'stu_tanya', student_name: 'Татьяна' }
    const c = card({ name: 'Татьяна', type: 'schoolchild', grade: '7' })
    const r = resolveStudentId('Татьяна', 'student', '', stored, c)
    expect(r.studentId).toBe(deriveStudentId('Татьяна', 'student', ''))
    expect(r.studentId).not.toBe('stu_tanya')
  })

  it('новый браузер (нет истории) → детерминированный id из identity', () => {
    const r = resolveStudentId('Иван Петров', 'student', '', {}, null)
    expect(r.studentId).toBe(deriveStudentId('Иван Петров', 'student', ''))
    expect(r.identity).toBe(deriveIdentityKey('Иван Петров', 'student', ''))
  })

  it('легаси id без identity на первом филле нового браузера не прилипает (карточка пустой профиль)', () => {
    // createSession уже создал случайный stu_random, но профиль пуст (карточка без префилла)
    const stored = { student_id: 'stu_random', student_name: '' }
    const c = card({}) // пустой профиль → префилл null
    const r = resolveStudentId('Иван Петров', 'student', '', stored, c)
    expect(r.studentId).toBe(deriveStudentId('Иван Петров', 'student', ''))
  })
})

describe('prefilledIdentity', () => {
  it('null на пустом профиле', () => {
    expect(prefilledIdentity(card({}))).toBeNull()
    expect(prefilledIdentity(null)).toBeNull()
    expect(prefilledIdentity({})).toBeNull()
  })

  it('строит identity из префилла профиля', () => {
    expect(prefilledIdentity(card({ name: 'Таня', type: 'schoolchild', grade: '7' })))
      .toBe(deriveIdentityKey('Таня', 'schoolchild', '7'))
  })
})