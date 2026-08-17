// SettingsGear — шестерёнка с настройкой «Быстрый ответ» (раздел 9.2)
import { useState } from 'react'

export default function SettingsGear({ quickAnswer, onToggle }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="settings-gear">
      <button
        className="gear-btn"
        onClick={() => setOpen((v) => !v)}
        title="Настройки"
        aria-label="Настройки"
        aria-expanded={open}
      >
        ⚙
      </button>
      {open && (
        <div className="settings-popup" role="dialog" aria-label="Настройки квиза">
          <div className="settings-header">Настройки</div>
          <label className="settings-toggle">
            <input
              type="checkbox"
              checked={quickAnswer}
              onChange={(e) => onToggle(e.target.checked)}
            />
            <span className="toggle-track">
              <span className="toggle-thumb" />
            </span>
            <span className="toggle-label">
              Быстрый ответ
              <small>{quickAnswer ? 'Клик по варианту = сразу отправить' : 'Клик по варианту = подтверждение'}</small>
            </span>
          </label>
          <button className="settings-close" onClick={() => setOpen(false)}>✕</button>
        </div>
      )}
    </div>
  )
}
