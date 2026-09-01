// ChatStream — стриминг событий агента (раздел 9.2)
// Source-сообщения схлопываются в одну карточку прогресса (timeline),
// чтобы не засорять чат. URL кликабельны, формулы рендерятся через LatexText.
import { useEffect, useRef, useMemo } from 'react'
import ExplanationPanel from './ExplanationPanel'
import LessonPanel from './LessonPanel'
import LatexText from './LatexText'

/** Извлекает домен из URL */
function extractDomain(text) {
  const m = String(text).match(/https?:\/\/([^/\s]+)/)
  return m ? m[1] : null
}

/** Иконка для каждого этапа source-прогресса */
function sourceIcon(text) {
  const t = String(text || '').toLowerCase()
  if (t.includes('построен граф')) return '🗺️'
  if (t.includes('собрано') || t.includes('готов') || t.includes('проиндекс')) return '✅'
  if (t.includes('индексац')) return '📑'
  if (t.includes('ищу') || t.includes('поиск')) return '🔍'
  if (t.includes('генерирую')) return '✨'
  if (t.includes('материал')) return '📚'
  if (t.includes('принято') || t.includes('начинаю')) return '▶️'
  return '🔎'
}

/** Делает URL кликабельными внутри текста */
function linkify(text) {
  const parts = String(text || '').split(/(https?:\/\/[^\s,;)]+)/g)
  if (parts.length <= 1) return text
  return parts.map((part, i) => {
    if (/^https?:\/\//.test(part)) {
      const domain = extractDomain(part)
      return (
        <a key={i} href={part} target="_blank" rel="noopener noreferrer" className="source-link">
          {domain || part}
        </a>
      )
    }
    return part ? <span key={i}>{part}</span> : null
  })
}

/**
 * Группирует последовательные source-сообщения в один блок,
 * чтобы не засорять чат 8+ отдельными пузырями.
 */
function groupFeed(feed) {
  const result = []
  let sourceGroup = null

  for (const m of feed) {
    if (m.kind === 'source') {
      if (!sourceGroup) {
        sourceGroup = { id: `src-group-${m.id}`, kind: 'source-group', items: [] }
      }
      sourceGroup.items.push(m)
    } else {
      if (sourceGroup) {
        result.push(sourceGroup)
        sourceGroup = null
      }
      result.push(m)
    }
  }
  if (sourceGroup) result.push(sourceGroup)
  return result
}

/** Карточка прогресса: схлопнутая группа source-сообщений */
function SourceProgressCard({ items }) {
  const lastItem = items[items.length - 1]
  const isComplete = items.some(i =>
    /собрано|готов|проиндекс|построен граф/i.test(i.text || '')
  )

  return (
    <div className={`source-progress-card ${isComplete ? 'complete' : 'active'}`}>
      <div className="source-progress-card__header">
        <span className="source-progress-card__icon">{isComplete ? '✅' : '⏳'}</span>
        <span className="source-progress-card__title">
          {isComplete ? 'Материалы собраны' : 'Подготовка материалов…'}
        </span>
        <span className="source-progress-card__count">{items.length} шагов</span>
      </div>
      <div className="source-progress-card__timeline">
        {items.map((item, i) => (
          <div key={item.id} className={`source-progress-card__step ${i === items.length - 1 ? 'current' : 'done'}`}>
            <span className="source-progress-card__step-icon">{sourceIcon(item.text)}</span>
            <span className="source-progress-card__step-text">{linkify(item.text)}</span>
          </div>
        ))}
      </div>
      {!isComplete && (
        <div className="source-progress-card__bar">
          <div className="progress-bar-indeterminate" />
        </div>
      )}
    </div>
  )
}

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

  const grouped = useMemo(() => groupFeed(feed), [feed])

  return (
    <div className="chatstream">
      {grouped.map((m) => (
        <div key={m.id} className={`msg ${m.kind}`}>
          {m.kind === 'user' && <div className="bubble user">{m.text}</div>}
          {m.kind === 'intake' && <div className="bubble agent intake">📋 {m.text}</div>}
          {m.kind === 'quiz' && <div className="bubble agent quiz">🎯 {m.text}</div>}
          {m.kind === 'lesson' && (
            <div className="bubble agent">
              <LessonPanel text={m.text} topic={m.data?.topic} lesson={m.data?.lesson} />
            </div>
          )}
          {m.kind === 'stream' && (
            <div className="bubble agent stream">
              <span className="stream-text"><LatexText text={m.text} /></span>
              {!m.streamEnded && <span className="stream-caret" aria-hidden="true" />}
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
          {m.kind === 'system' && (
            <div className="bubble system">
              <span className="system-icon">ℹ️</span>
              <span className="system-text">{linkify(m.text)}</span>
            </div>
          )}
          {m.kind === 'source-group' && (
            <SourceProgressCard items={m.items} />
          )}
          {m.kind === 'source' && (
            <div className="bubble source">
              <span className="source-bubble-icon">{sourceIcon(m.text)}</span>
              <span className="source-bubble-text">{linkify(m.text)}</span>
            </div>
          )}
          {m.kind === 'agent' && <div className="bubble agent tutor-chat">🎓 <LatexText text={m.text} /></div>}
          {m.kind === 'hint' && (
            <div className="bubble agent hint">💡 <LatexText text={m.text} /></div>
          )}
          {m.kind === 'subtask' && (
            <div className="bubble agent subtask">
              <div className="subtask-badge">
                Шаг {m.data?.subtask_index ?? '?'} из {m.data?.subtask_total ?? '?'}
              </div>
              <LatexText text={m.text} />
            </div>
          )}
          {m.kind === 'review' && <div className="bubble review">🔁 {m.text}</div>}
          {m.kind === 'error' && <div className="bubble error">⚠️ {m.text}</div>}
        </div>
      ))}
      {busy && (
        <div className="msg progress-indicator">
          <div className={`bubble agent progress${progressPhase ? ' has-phase' : ''}`}>
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
          <div className="mark" aria-hidden="true"><span /></div>
          <h2>Готовим занятие…</h2>
          <p>
            Тьютор готовит первый вопрос. Можно загрузить учебник или подождать секунду.
          </p>
        </div>
      )}
      <div ref={endRef} />
    </div>
  )
}
