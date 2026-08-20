// SourceSearchPanel — статус авто-поиска/индексации учебника + найденные источники (раздел 9.2)
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

export default function SourceSearchPanel({ status, note, sources = [], author = null, onFind, busy = false }) {
  const list = Array.isArray(sources) ? sources : []
  const webPages = list.filter((s) => s && s.type === 'page' && s.url)
  const localPdf = list.filter((s) => s && s.type === 'local_pdf')

  return (
    <div className="card source">
      <h3>Источник</h3>
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
      <button onClick={onFind} className="btn" disabled={busy}>
        {busy ? 'Работаю…' : 'Найти учебник'}
      </button>
    </div>
  )
}
