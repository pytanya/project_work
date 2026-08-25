// SourceWhitelistPanel — политика источников ученика (раздел 8.5):
// toggle «Любые источники» + белый список доменов (редактируемый).
import { useEffect, useState } from 'react'

export default function SourceWhitelistPanel({ studentId = '', onChanged, openSignal = 0 }) {
  const [allowAny, setAllowAny] = useState(true)
  const [whitelist, setWhitelist] = useState([])
  const [input, setInput] = useState('')
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)

  // Внешний сигнал «раскрыть панель» (кнопка «Изменить источники» в предложении)
  useEffect(() => {
    if (openSignal > 0) setOpen(true)
  }, [openSignal])

  useEffect(() => {
    if (!studentId) return
    let cancelled = false
    fetch(`/api/students/${encodeURIComponent(studentId)}/sources`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body) => {
        if (cancelled) return
        setAllowAny(Boolean(body.allow_any_sources))
        setWhitelist(Array.isArray(body.whitelist) ? body.whitelist : [])
        setInput(Array.isArray(body.whitelist) ? body.whitelist.join(', ') : '')
      })
      .catch((e) => !cancelled && setError(String(e.message || e)))
    return () => { cancelled = true }
  }, [studentId])

  const persist = async (body) => {
    if (!studentId || loading) return null
    setLoading(true)
    setError(null)
    setSaved(false)
    try {
      const res = await fetch(`/api/students/${encodeURIComponent(studentId)}/sources`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const saved = await res.json()
      setAllowAny(Boolean(saved.allow_any_sources))
      setWhitelist(Array.isArray(saved.whitelist) ? saved.whitelist : [])
      setInput(Array.isArray(saved.whitelist) ? saved.whitelist.join(', ') : '')
      setSaved(true)
      if (onChanged) onChanged(saved)
      setTimeout(() => setSaved(false), 2500)
      return saved
    } catch (e) {
      setError(String(e.message || e))
      return null
    } finally {
      setLoading(false)
    }
  }

  const save = async () => {
    const nextList = parseDomains()
    if (!allowAny && nextList.length === 0) {
      setError('Добавьте хотя бы один домен в белый список — иначе поиск будет заблокирован.')
      return
    }
    await persist({ allow_any_sources: allowAny, whitelist: nextList })
  }

  const toggleAny = () => {
    // Выключение «любых» открывает редактор; пустой список сохранится как есть
    // (поиск будет заблокирован до добавления доменов — это честное состояние).
    if (allowAny) setOpen(true)
    persist({ allow_any_sources: !allowAny, whitelist })
  }

  const parseDomains = () =>
    input.split(/[\s,;]+/).map((d) => d.trim()).filter(Boolean)

  return (
    <div className="card source-policy">
      <button className="source-policy__toggle" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="source-policy__title">Источники</span>
        <span className="source-policy__state">{allowAny ? 'любые' : `список · ${whitelist.length}`}</span>
        <span className="source-policy__caret">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <div className="source-policy__body">
          <label className="source-policy__any">
            <input type="checkbox" checked={allowAny} onChange={toggleAny} disabled={loading} />
            <span>
              Любые источники
              <small>искать без ограничений по сайтам</small>
            </span>
          </label>
          {!allowAny && (
            <div className="source-policy__list">
              <label className="source-policy__label" htmlFor="whitelist-input">
                Белый список доменов
                <small>через запятую: wikibooks.org, yaklass.ru</small>
              </label>
              <textarea
                id="whitelist-input"
                rows={3}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="wikibooks.org, wikipedia.org, lc.rt.ru"
                disabled={loading}
              />
              <button className="btn small" onClick={save} disabled={loading}>
                {loading ? 'Сохраняем…' : 'Сохранить'}
              </button>
            </div>
          )}
          {saved && <div className="source-policy__note ok">✓ Политика источников сохранена</div>}
          {error && <div className="source-policy__note err">Не удалось сохранить: {error}</div>}
        </div>
      )}
    </div>
  )
}
