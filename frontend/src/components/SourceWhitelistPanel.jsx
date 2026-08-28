// SourcesPanel — единая панель «Источники» (объединяет бывшие SourceWhitelistPanel +
// SourceSearchPanel): статус авто-поиска/индексации, найденные источники, кнопка
// «Найти учебник» и политика источников (любые / белый список доменов).
import { useEffect, useState } from 'react'

const STATUS_LABELS = {
  ready: 'Источник готов',
  failed: 'Источник не найден',
  indexing: 'Обработка…',
  searching: 'Поиск…',
  idle: 'Источник не выбран',
}

function hostOf(url) {
  try {
    return new URL(url).hostname
  } catch {
    return url
  }
}

export default function SourceWhitelistPanel({
  studentId = '', onChanged, openSignal = 0,
  status = null, note = null, sources = [], author = null, onFind, busy = false,
}) {
  const [allowAny, setAllowAny] = useState(true)
  const [whitelist, setWhitelist] = useState([])
  const [input, setInput] = useState('')
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [saved, setSaved] = useState(false)

  const list = Array.isArray(sources) ? sources : []
  const webPages = list.filter((s) => s && s.type === 'page' && s.url)
  const localPdf = list.filter((s) => s && s.type === 'local_pdf')

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

      {/* Статус авто-поиска/индексации + найденные источники — всегда видны
          (ранее SourceSearchPanel), не прячутся за аккордеоном политики. */}
      <div className="source-policy__status">
        <div className={`source-status ${status || 'idle'}`}>
          {STATUS_LABELS[status] || status || STATUS_LABELS.idle}
        </div>
        {note && <div className="source-note">{note}</div>}
        {author && (
          <div className="source-author">
            Автор: <strong>{author}</strong>
          </div>
        )}
        {webPages.length > 0 && (
          <div className="source-list">
            <div className="source-list-title">Найденные источники ({webPages.length}):</div>
            <ul>
              {webPages.map((s, i) => (
                <li key={`${s.url}-${i}`}>
                  <a href={s.url} target="_blank" rel="noopener noreferrer" title={s.url}>
                    {s.title || hostOf(s.url)}
                  </a>
                  {s.license && <span className="muted"> · {s.license}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {localPdf.length > 0 && (
          <div className="source-list">
            <div className="source-list-title">Локальный учебник:</div>
            <ul>
              {localPdf.map((s, i) => (
                <li key={`${s.path}-${i}`}>{s.path || 'PDF'}</li>
              ))}
            </ul>
          </div>
        )}
        {webPages.length === 0 && localPdf.length === 0 && (status === 'ready' || status === 'done') && (
          <div className="source-note muted" style={{ fontSize: '12px', padding: '4px 0' }}>
            Источник проиндексирован, материал готов к использованию
          </div>
        )}
        <button onClick={onFind} className="btn" disabled={busy}>
          {busy ? 'Работаю…' : 'Найти учебник'}
        </button>
      </div>

      {open && (
        <div className="source-policy__body">
          {/* Политика источников: любые / белый список */}
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
