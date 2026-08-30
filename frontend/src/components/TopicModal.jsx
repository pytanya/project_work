// TopicModal — чтение темы из базы знаний (источник + изложение + понятия + заметки).
// Рендерится через портал в document.body: иначе position:fixed «прилипает» к сайдбару
// (backdrop-filter создаёт containing-block) и окно не отстыковывается от панели.
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import NoteItem from './NoteItem'
import LatexText from './LatexText'

function masteryClass(m) {
  if (m >= 0.75) return 'high'
  if (m >= 0.45) return 'mid'
  return 'low'
}

/**
 * Уровень мастерства для emoji-иконки
 */
function levelEmoji(m) {
  if (m >= 0.75) return '🟢'
  if (m >= 0.45) return '🟡'
  return '🔴'
}

const PLACEHOLDER = /^Материал по теме .+ накапливается/

export default function TopicModal({ article, subject, onClose, onEnrich, enriching = false, enrichNote = null }) {
  const [progressExpanded, setProgressExpanded] = useState(false)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  if (!article) return null
  const pct = Math.round((article.mastery || 0) * 100)
  const accPct = article.attempts > 0 ? Math.round(((article.correct || 0) / article.attempts) * 100) : 0
  const cls = masteryClass(article.mastery)
  const hasBody = Boolean(article.body && article.body.trim() && !PLACEHOLDER.test(article.body))
  const showCurriculum = Boolean(article.curriculum && article.curriculum !== 'unverified')

  // Notes массив (поддерживаются и объекты, и legacy-строки — NoteItem сам парсит)
  const notes = Array.isArray(article.notes) ? article.notes : []

  return createPortal(
    <div className="topic-modal-backdrop" onClick={onClose}>
      <div className="topic-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <button className="topic-modal__close" onClick={onClose} title="Закрыть (Esc)">✕</button>
        <div className="topic-modal__meta">
          <span className="topic-modal__subject">{subject || 'тема'}</span>
          {article.grade && <span className="topic-modal__badge">класс {article.grade}</span>}
          {showCurriculum && <span className="topic-modal__badge" title="программа/ФГОС">{article.curriculum}</span>}
          {article.okf_version && <span className="topic-modal__badge okf">OKF {article.okf_version}</span>}
        </div>
        <h2 className="topic-modal__title"><LatexText text={article.title || article.topic} /></h2>

        {/* Прогресс: отдельный визуальный блок с аккордеоном */}
        <div className={`topic-modal__progress ${cls}`}>
          <button 
            className="topic-modal__progress-header"
            onClick={() => setProgressExpanded(!progressExpanded)}
            aria-expanded={progressExpanded}
          >
            <span className="topic-modal__progress-level">{levelEmoji(article.mastery || 0)}</span>
            <span className="topic-modal__progress-title">Прогресс освоения</span>
            <span className={`topic-modal__progress-arrow ${progressExpanded ? 'open' : ''}`}>▾</span>
          </button>
          
          <div className="topic-modal__progress-main">
            <div className="topic-modal__progress-bar-wrap">
              <div className="topic-modal__progress-bar-track">
                <div className={`topic-modal__progress-bar-fill ${cls}`} style={{ width: `${pct}%` }} />
              </div>
              <span className="topic-modal__progress-pct">{pct}%</span>
            </div>
            <div className="topic-modal__progress-stats">
              <span className="topic-modal__progress-stat">
                <span className="topic-modal__progress-stat-label">Попытки:</span>
                <span className="topic-modal__progress-stat-value">{article.attempts ?? 0}</span>
              </span>
              <span className="topic-modal__progress-stat">
                <span className="topic-modal__progress-stat-label">Точность:</span>
                <span className="topic-modal__progress-stat-value">{accPct}%</span>
              </span>
            </div>
          </div>

          {progressExpanded && (article.correct !== undefined) && (
            <div className="topic-modal__progress-details">
              <div className="topic-modal__progress-detail-row">
                <span>Верных ответов:</span>
                <strong>{article.correct ?? 0}</strong>
              </div>
              <div className="topic-modal__progress-detail-row">
                <span>Неверных ответов:</span>
                <strong>{(article.attempts ?? 0) - (article.correct ?? 0)}</strong>
              </div>
              <div className="topic-modal__progress-detail-row">
                <span>Уровень мастерства:</span>
                <strong>{(article.mastery ?? 0).toFixed(2)}</strong>
              </div>
            </div>
          )}
        </div>

        {article.source && (
          <div className="topic-modal__source">
            Источник: <span className="topic-modal__source-value">{article.source}</span>
          </div>
        )}

        {Array.isArray(article.concepts) && article.concepts.length > 0 && (
          <div className="topic-modal__concepts">
            {article.concepts.map((c, i) => <span key={i} className="topic-modal__concept">{c}</span>)}
          </div>
        )}

        {hasBody && (
          <div className="topic-modal__body">
            <div className="topic-modal__section-label">Изложение темы</div>
            <div className="topic-modal__body-text"><LatexText text={article.body} /></div>
          </div>
        )}

        {!hasBody && onEnrich && (
          <>
            <button className="topic-modal__enrich" onClick={onEnrich} disabled={enriching}>
              {enriching ? 'Генерирую изложение…' : 'Сгенерировать изложение'}
            </button>
            {enrichNote && <div className="topic-modal__enrich-note">{enrichNote}</div>}
          </>
        )}

        {/* Заметки */}
        {notes.length > 0 && (
          <div className="topic-modal__notes">
            <div className="topic-modal__section-label">Заметки ({notes.length})</div>
            
            {notes.map((note, i) => (
              <NoteItem key={`note-${i}`} note={note} index={i} />
            ))}
          </div>
        )}
      </div>
    </div>,
    document.body,
  )
}
