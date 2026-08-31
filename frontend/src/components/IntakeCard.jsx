// IntakeCard — карточка знакомства: все поля чек-листа сразу (быстрое заполнение).
// Генерируется агентом/детерминированно (state.agent_card), ученик заполняет форму
// и отправляет одним POST /intake/card — без долгих вопросов по одному.
import { useState } from 'react'

function humanizeOptions(options) {
  return (options || []).map((o) => (typeof o === 'string' ? { value: o, label: o } : o))
}

export default function IntakeCard({ card, onSubmit, disabled }) {
  const initial = {}
  for (const f of card?.fields || []) initial[f.key] = f.value ?? ''
  const [values, setValues] = useState(initial)
  const [sending, setSending] = useState(false)

  const setField = (key, val) => setValues((v) => ({ ...v, [key]: val }))

  const requiredMissing = (card?.fields || [])
    .filter((f) => f.required && !String(values[f.key] ?? '').trim())
    .map((f) => f.label)

  // ФИО: имя + фамилия (минимум два слова) — ключ персональной изоляции
  const nameWords = String(values.name ?? '').trim().split(/\s+/).filter(Boolean).length
  const nameInvalid = values.name !== undefined && values.name !== '' && nameWords < 2
  const badName = !String(values.name ?? '').trim() ? null : nameInvalid ? 'Укажи имя и фамилию (минимум два слова).' : null

  const submit = async () => {
    if (requiredMissing.length > 0 || badName || sending || disabled) return
    setSending(true)
    try {
      await onSubmit(values)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="card intake-card">
      <h3>{card?.title || 'Знакомство и план занятия'}</h3>
      {card?.question && <div className="question-text">{card.question}</div>}
      <div className="intake-card__fields">
        {(card?.fields || []).map((f) => {
          const label = f.required ? `${f.label} *` : f.label
          if (f.type === 'choice') {
            return (
              <label key={f.key} className="intake-field">
                <span className="intake-field__label">{label}</span>
                <select
                  value={String(values[f.key] ?? '')}
                  onChange={(e) => setField(f.key, e.target.value)}
                  disabled={sending || disabled}
                >
                  <option value="">— выберите —</option>
                  {humanizeOptions(f.options).map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </label>
            )
          }
          return (
            <label key={f.key} className="intake-field">
              <span className="intake-field__label">{label}</span>
              <input
                type="text"
                value={values[f.key] ?? ''}
                placeholder={f.placeholder || ''}
                onChange={(e) => setField(f.key, e.target.value)}
                disabled={sending || disabled}
              />
            </label>
          )
        })}
      </div>
      {requiredMissing.length > 0 && (
        <div className="intake-card__hint muted">Заполните: {requiredMissing.join(', ')}</div>
      )}
      {badName && (
        <div className="intake-card__hint muted">Укажи имя и фамилию (минимум два слова).</div>
      )}
      <button className="btn-primary" onClick={submit} disabled={sending || disabled || requiredMissing.length > 0 || !!badName}>
        {sending ? 'Отправляем…' : 'Начать занятие'}
      </button>
    </div>
  )
}
