// SourceSearchPanel — статус авто-поиска учебника (раздел 9.2)
export default function SourceSearchPanel({ status, note, onFind }) {
  return (
    <div className="card source">
      <h3>Источник</h3>
      <div className={`source-status ${status || 'idle'}`}>
        {status ? `Статус: ${status}` : 'Источник не найден'}
      </div>
      {note && <div className="source-note">{note}</div>}
      <button onClick={onFind} className="btn">
        Найти учебник
      </button>
    </div>
  )
}
