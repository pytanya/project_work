// ChatStream — стриминг событий агента (раздел 9.2)
import { useEffect, useRef } from 'react'
import ExplanationPanel from './ExplanationPanel'
import LessonPanel from './LessonPanel'

export default function ChatStream({ feed, busy = false, progressPhase = null }) {
  const endRef = useRef(null)
  useEffect(() => {
    try {
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches
      endRef.current?.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'end' })
    } catch (_) {
      /* jsdom не реализует scrollIntoView — в тестах тихо */
    }
  }, [feed, busy])

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
          {m.kind === 'stream' && (
            <div className="bubble agent stream">
              <span className="stream-text">{m.text}</span>
              <span className="stream-caret" aria-hidden="true" />
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
      {busy && (
        <div className="msg progress-indicator">
          <div className="bubble agent progress">
            {progressPhase ? (
              <>
                <div className="progress-text">{progressPhase.message}</div>
                <div className="progress-bar-wrap">
                  <div className="progress-bar-indeterminate" />
                </div>
              </>
            ) : (
              <><span className="dot" /><span className="dot" /><span className="dot" /></>
            )}
          </div>
        </div>
      )}
      {feed.length === 0 && !busy && (
        <div className="empty-chat">
          <div className="mark" aria-hidden="true" />
          <h2>Сессия создаётся…</h2>
          <p>
            Ответьте на вопросы тьютора внизу или загрузите учебник — и начнём урок.
          </p>
        </div>
      )}
      <div ref={endRef} />
    </div>
  )
}

