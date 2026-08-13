// ChatStream — стриминг событий агента (раздел 9.2)
import ExplanationPanel from './ExplanationPanel'
import LessonPanel from './LessonPanel'

export default function ChatStream({ feed }) {
  return (
    <div className="chatstream">
      {feed.map((m) => (
        <div key={m.id} className={`msg ${m.kind}`}>
          {m.kind === 'user' && <div className="bubble user">{m.text}</div>}
          {m.kind === 'intake' && <div className="bubble agent intake">📋 {m.text}</div>}
          {m.kind === 'quiz' && <div className="bubble agent quiz">🎯 {m.text}</div>}
          {m.kind === 'lesson' && (
            <div className="bubble agent">
              <LessonPanel text={m.text} topic={m.data?.topic} />
            </div>
          )}
          {m.kind === 'explanation' && (
            <div className="bubble agent">
              <ExplanationPanel
                text={m.data?.message || m.text}
                citation={m.data?.citation}
              />
            </div>
          )}
          {m.kind === 'summary' && <div className="bubble summary">✅ {m.text}</div>}
          {m.kind === 'system' && <div className="bubble system">ℹ️ {m.text}</div>}
          {m.kind === 'source' && <div className="bubble source">🔎 {m.text}</div>}
          {m.kind === 'error' && <div className="bubble error">⚠️ {m.text}</div>}
        </div>
      ))}
      {feed.length === 0 && <div className="muted center">Сессия создаётся…</div>}
    </div>
  )
}
