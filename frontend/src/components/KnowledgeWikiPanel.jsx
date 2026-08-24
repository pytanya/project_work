// KnowledgeWikiPanel — card-based knowledge base (roadmap #2 redesign):
// subject sections, topic cards with mastery progress bars, stats, notes.
// Клик по теме → модальное окно чтения (источник + изложение + понятия).
import { useEffect, useState, useMemo } from 'react'
import MasteryWall from './MasteryWall'
import TopicModal from './TopicModal'

function masteryClass(m) {
  if (m >= 0.75) return 'high'
  if (m >= 0.45) return 'mid'
  return 'low'
}

export default function KnowledgeWikiPanel() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [expandedSubject, setExpandedSubject] = useState(null)
  const [reading, setReading] = useState(null) // {subject, article}
  const [enriching, setEnriching] = useState(false)
  const [enrichNote, setEnrichNote] = useState(null)
  const [filter, setFilter] = useState('')

  useEffect(() => {
    let cancelled = false
    fetch('/api/wiki')
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((body) => !cancelled && setData(body.subjects || []))
      .catch((e) => !cancelled && setError(String(e.message || e)))
    return () => { cancelled = true }
  }, [])

  const subjects = data || []
  const total = subjects.reduce((n, s) => n + (s.articles?.length || 0), 0)

  // Aggregate stats
  const stats = useMemo(() => {
    let attempts = 0, correct = 0, topics = 0
    for (const s of subjects) {
      for (const a of s.articles || []) {
        topics++
        attempts += a.attempts || 0
        correct += a.correct || 0
      }
    }
    const avgMastery = topics > 0
      ? subjects.reduce((sum, s) => sum + (s.articles || []).reduce((ss, a) => ss + (a.mastery || 0), 0), 0) / topics
      : 0
    return { topics, attempts, correct, avgMastery }
  }, [subjects])

  // Filter articles (темы + ключевые понятия + предмет)
  const filteredSubjects = useMemo(() => {
    if (!filter.trim()) return subjects
    const q = filter.trim().toLowerCase()
    return subjects.map((s) => ({
      ...s,
      articles: (s.articles || []).filter((a) =>
        (a.title || a.topic || '').toLowerCase().includes(q) ||
        (Array.isArray(a.concepts) && a.concepts.some((c) => String(c).toLowerCase().includes(q))) ||
        (s.subject || '').toLowerCase().includes(q)
      ),
    })).filter((s) => s.articles.length > 0)
  }, [subjects, filter])

  if (error) return (
    <div className="card wiki-panel">
      <h3>База знаний</h3>
      <div className="muted" style={{ color: 'var(--err)' }}>Не удалось загрузить: {error}</div>
    </div>
  )

  const selectTopic = (subject, topic) => {
    const si = subjects.findIndex((s) => (s.subject || '') === (subject || ''))
    if (si < 0) return
    const ai = (subjects[si].articles || []).findIndex((a) => (a.topic || a.title) === topic)
    if (ai < 0) return
    setExpandedSubject(si)
    setReading({ subject: subjects[si].subject || 'тема', article: subjects[si].articles[ai] })
  }

  const enrichTopic = async () => {
    if (!reading) return
    setEnriching(true)
    setEnrichNote(null)
    try {
      const res = await fetch('/api/wiki/enrich', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject: reading.subject, topic: reading.article.topic || reading.article.title }),
      })
      if (res.ok) {
        const b = await res.json()
        if (b.article) setReading({ subject: reading.subject, article: b.article })
        if (b.note) setEnrichNote(b.note)
      } else {
        setEnrichNote('Не удалось сгенерировать изложение (ошибка сервера).')
      }
    } catch (_) {
      setEnrichNote('Не удалось сгенерировать изложение: нет связи с сервером.')
    }
    setEnriching(false)
  }

  return (
    <div className="card wiki-panel">
      <h3>База знаний · {total}</h3>

      {total === 0 && (
        <div className="wiki-empty">
          <div className="wiki-empty__icon">📖</div>
          <div className="wiki-empty__title">Знания накапливаются</div>
          <div className="wiki-empty__text">
            Пройдите квиз по теме, и она появится здесь с вашим прогрессом, заметками и статистикой.
          </div>
        </div>
      )}

      {total > 0 && (
        <>
          {/* Отдельный схлапываемый визуал усвоения (heat-map по предметам) */}
          <MasteryWall subjects={filteredSubjects} onSelect={selectTopic} />
          {/* Summary stats bar */}
          <div className="wiki-stats-bar">
            <div className="wiki-stat">
              <span className="wiki-stat__value">{stats.topics}</span>
              <span className="wiki-stat__label">тем</span>
            </div>
            <div className="wiki-stat">
              <span className="wiki-stat__value">{stats.attempts}</span>
              <span className="wiki-stat__label">попыток</span>
            </div>
            <div className="wiki-stat">
              <span className="wiki-stat__value">{Math.round(stats.avgMastery * 100)}%</span>
              <span className="wiki-stat__label">ср. мастерство</span>
            </div>
            <div className="wiki-stat">
              <span className="wiki-stat__value">
                {stats.attempts > 0 ? Math.round((stats.correct / stats.attempts) * 100) : 0}%
              </span>
              <span className="wiki-stat__label">точность</span>
            </div>
          </div>

          {/* Search */}
          {total > 3 && (
            <input className="topic-search" placeholder="🔍 Фильтр тем…"
              value={filter} onChange={(e) => setFilter(e.target.value)} />
          )}

          {/* Subject sections */}
          <div className="wiki-subjects">
            {filteredSubjects.map((s, si) => {
              const isExpanded = expandedSubject === si || filteredSubjects.length === 1
              return (
                <div key={s.subject || si} className="wiki-subject">
                  <button className={`wiki-subject__header ${isExpanded ? 'open' : ''}`}
                    onClick={() => setExpandedSubject(isExpanded ? null : si)}>
                    <span className="wiki-subject__arrow">{isExpanded ? '▾' : '▸'}</span>
                    <span className="wiki-subject__name">{s.subject || 'Без предмета'}</span>
                    <span className="wiki-subject__count">{(s.articles || []).length}</span>
                  </button>
                  {isExpanded && (
                    <div className="wiki-articles">
                      {(s.articles || []).map((a, ai) => {
                        const key = `${si}-${ai}`
                        const pct = Math.round((a.mastery || 0) * 100)
                        return (
                          <div key={key} className="wiki-article">
                            <button className="wiki-article__row"
                              onClick={() => setReading({ subject: s.subject || 'тема', article: a })}
                              title="Открыть для чтения">
                              <span className={`wiki-article__dot ${masteryClass(a.mastery)}`} />
                              <span className="wiki-article__title">{a.title || a.topic}</span>
                              {a.okf_version && <span className="wiki-article__src" title="источник OKF/LLM-wiki">OKF</span>}
                              <span className="wiki-article__pct">{pct}%</span>
                            </button>
                            <div className="wiki-article__bar-bg">
                              <div className={`wiki-article__bar-fill ${masteryClass(a.mastery)}`}
                                style={{ width: `${pct}%` }} />
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              )
            })}
            {filteredSubjects.length === 0 && filter && (
              <div className="muted" style={{ padding: '10px 0' }}>Ничего не найдено</div>
            )}
          </div>
        </>
      )}
      <TopicModal article={reading?.article} subject={reading?.subject}
        onClose={() => setReading(null)} onEnrich={enrichTopic} enriching={enriching}
        enrichNote={enrichNote} />
    </div>
  )
}
