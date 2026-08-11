// SourceSearchPanel — статус авто-поиска/индексации учебника (раздел 9.2)
const STATUS_LABELS = {
  ready: 'Источник готов',
  failed: 'Источник не найден',
  indexing: 'Обработка…',
  searching: 'Поиск…',
  idle: 'Источник не выбран',
}

export default function SourceSearchPanel({ status, note, onFind, busy = false }) {
  return (
    <div className="card source">
      <h3>Источник</h3>
      <div className={`source-status ${status || 'idle'}`}>
        {STATUS_LABELS[status] || status || STATUS_LABELS.idle}
      </div>
      {note && <div className="source-note">{note}</div>}
      <button onClick={onFind} className="btn" disabled={busy}>
        {busy ? 'Работаю…' : 'Найти учебник'}
      </button>
    </div>
  )
}
