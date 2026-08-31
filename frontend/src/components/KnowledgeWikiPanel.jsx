// KnowledgeWikiPanel — card-based knowledge base (roadmap #2 redesign):
// subject sections, topic cards with mastery progress bars, stats, notes.
// Клик по теме → модальное окно чтения (источник + изложение + понятия).
import { useEffect, useState, useMemo, useRef } from 'react'
import LatexText from './LatexText'
import MasteryWall from './MasteryWall'
import TopicModal from './TopicModal'

function masteryClass(m) {
  if (m >= 0.75) return 'high'
  if (m >= 0.45) return 'mid'
  return 'low'
}

/* Темы-«мусор» от веб-скрапинга: домены/URL-слаги вместо названий (multiurok.ru, yandex.ru…) */
const URL_LIKE_RE = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z]{2,})+(?:\/[^\s]*)?$/i
const DOMAIN_NOISE = /footer|toggle|menu|sidebar|navbar|login|signin|register|cookie/i
// Навигационные/скрап-фрагменты страниц порталов, ошибочно попавшие в темы
// базы знаний: «Картинки», «Тесты», «Параграф 24», «По теме: методические…» и т.п.
const WIKI_JUNK_EXACT = new Set([
  'картинки', 'картинка', 'тесты', 'тест', 'задания', 'задание', 'фильтры', 'фильтр',
  'теория', 'содержание', 'введение', 'заключение', 'вывод', 'выводы', 'итоги', 'итог',
  'источники', 'источник', 'вопросы', 'вопрос', 'ответы', 'ответ', 'главная', 'меню',
  'далее', 'назад', 'рефлексия', 'проблема', 'цели', 'цель', 'задачи', 'задача',
  'план', 'план-конспект', 'конспект', 'презентация', 'список литературы',
  'используемая литература', 'рекомендуемая литература', 'спасибо за внимание',
  'шаблон', 'эпиграф', 'тема урока', 'цель урока', 'проверка домашнего задания',
  'организационный момент', 'актуализация знаний', 'мотивация', 'физминутка',
])
const WIKI_JUNK_PREFIX = /^(параграф\s*\d+|урок\s*\d+|слайд\s*\d+|часть\s*\d+|по теме: методические|похожие|вернуться|вернутся)/i
const WIKI_JUNK_SUBSTR = /методические разработки|материалы для учителей|улучшить свой запрос|место проведения|название проекта|свою работу я оцениваю|главным своим результатом|что я узнал|остался вопрос|домашнее задание|презентацию подготовила|выполнила:|физминутк|рефлекси|актуализаци|мотиваци/i

function isJunkTopic(title) {
  if (!title) return true
  const t = title.trim()
  const low = t.toLowerCase()
  if (URL_LIKE_RE.test(t)) return true
  if (DOMAIN_NOISE.test(t)) return true
  if (WIKI_JUNK_EXACT.has(low)) return true
  if (WIKI_JUNK_PREFIX.test(t)) return true
  if (WIKI_JUNK_SUBSTR.test(t)) return true
  return false
}

export default function KnowledgeWikiPanel({ studentId = '', studentName = '', intakeComplete = false }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [expandedSubject, setExpandedSubject] = useState(null)
  const [reading, setReading] = useState(null) // {subject, article}
  const [enriching, setEnriching] = useState(false)
  const [enrichNote, setEnrichNote] = useState(null)
  const [filter, setFilter] = useState('')
  const cancelledRef = useRef(false)

  // Персональная база знаний: ?student_id= изолирует данные разных учеников
  const fetchWiki = async () => {
    if (!studentId) return
    cancelledRef.current = false
    const q = studentId ? `?student_id=${encodeURIComponent(studentId)}` : ''
    try {
      const r = await fetch(`/api/wiki${q}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const body = await r.json()
      if (!cancelledRef.current) setData(body.subjects || [])
    } catch (e) {
      if (!cancelledRef.current) setError(String(e.message || e))
    }
  }

  useEffect(() => {
    // База знаний строится по материалам темы: загружаем, когда карточка
    // заполнена (ученик/тема/класс сопоставлены) — вернувшийся ученик сразу
    // видит свою закешированную базу, новые темы добавляются после сбора
    // материалов и урока/квиза.
    if (!studentId || !intakeComplete) return
    fetchWiki()
    return () => { cancelledRef.current = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [studentId, intakeComplete])

  // Отсев URL-мусора от веб-скрапинга (multiurok.ru, footer#toggle и т.п.)
  const subjects = useMemo(() => (data || [])
    .map((s) => ({ ...s, articles: (s.articles || []).filter((a) => !isJunkTopic(a.title || a.topic || '')) }))
    .filter((s) => s.articles.length > 0), [data])
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
    const q = filter.trim().toLowerCase()
    if (!q) return subjects
    return subjects
      .map((s) => ({
        ...s,
        articles: (s.articles || []).filter((a) =>
          (a.title || a.topic || '').toLowerCase().includes(q) ||
          (Array.isArray(a.concepts) && a.concepts.some((c) => String(c).toLowerCase().includes(q))) ||
          (s.subject || '').toLowerCase().includes(q)
        ),
      }))
      .filter((s) => s.articles.length > 0)
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

  const deleteTopic = async () => {
    if (!reading) return
    const subject = reading.subject
    const topic = reading.article.topic || reading.article.title
    setEnriching(false)
    setEnrichNote(null)
    try {
      const res = await fetch(`/api/wiki/${encodeURIComponent(subject)}/${encodeURIComponent(topic)}?student_id=${encodeURIComponent(studentId || '')}`, {
        method: 'DELETE',
      })
      if (res.ok) {
        setReading(null)
        await fetchWiki()
      } else {
        setEnrichNote('Не удалось удалить карточку (ошибка сервера).')
      }
    } catch (_) {
      setEnrichNote('Не удалось удалить карточку: нет связи с сервером.')
    }
  }

  const enrichTopic = async () => {
    if (!reading) return
    setEnriching(true)
    setEnrichNote(null)
    try {
      const res = await fetch('/api/wiki/enrich', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ subject: reading.subject, topic: reading.article.topic || reading.article.title, student_id: studentId }),
      })
      if (res.ok) {
        const b = await res.json()
        if (b.article) {
          setReading({ subject: reading.subject, article: b.article })
          // Патчим data-state напрямую: при повторном открытии модала body не потеряется
          // (даже если fetchWiki ещё не завершился или компонент перерисовался).
          setData((prev) => (prev || []).map((s) => ({
            ...s,
            articles: (s.articles || []).map((a) =>
              (a.topic || a.title) === (b.article.topic || b.article.title) ? b.article : a
            ),
          })))
        }
        if (b.note) setEnrichNote(b.note)
        // Изложение сохранено на бэкенде — обновляем список с сервера для надёжности.
        await fetchWiki()
      } else {
        setEnrichNote('Не удалось сгенерировать изложение (ошибка сервера).')
      }
    } catch (_) {
      setEnrichNote('Не удалось сгенерировать изложение: нет связи с сервером.')
    }
    setEnriching(false)
  }

  // Карточка не заполнена (ученик/тема/класс ещё не сопоставлены) — база знаний
  // строится из собранных по теме материалов, поэтому пока пустой экран.
  if (!intakeComplete) {
    return (
      <div className="card wiki-panel">
        <h3>База знаний</h3>
        <div className="wiki-empty">
          <div className="wiki-empty__icon">📝</div>
          <div className="wiki-empty__title">Заполните карточку ученика</div>
          <div className="wiki-empty__text">
            Сопоставим вас с темой и классом, соберём материалы — и здесь появятся темы,
            понятия и ваш прогресс.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="card wiki-panel">
      <h3>База знаний{studentName ? ` · ${studentName}` : ''} · {total}</h3>

      {total === 0 && (
        <div className="wiki-empty">
          <div className="wiki-empty__icon">📖</div>
          <div className="wiki-empty__title">Знания накапливаются</div>
          <div className="wiki-empty__text">
            Соберите материалы по теме и пройдите урок или квиз — темы и понятия появятся здесь
            с вашим прогрессом, заметками и статистикой.
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
                              <span className="wiki-article__title"><LatexText text={a.title || a.topic} /></span>
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
        enrichNote={enrichNote} onDelete={deleteTopic} />
    </div>
  )
}
