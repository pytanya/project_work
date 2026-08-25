// LessonPanel — урок по теме (режим lesson).
// Карточное представление структурированного урока (LessonSchema).
import { useMemo, useState } from 'react'
import LatexText from './LatexText'
import LessonDiagram from './LessonDiagram'

/* ═══════════════════════════════════════════
   Утилиты санитизации
   ═══════════════════════════════════════════ */

/** Отфильтровывает JSON-подобные значения от модели, чтобы в UI не появлялся сырой JSON */
function clean(v, field) {
  if (field === undefined && typeof v === 'string') field = 'unknown'
  const s = String(v ?? '').trim().replace(/^\ufeff/, '')
  if (!s) return ''
  if (s.startsWith('{') || s.startsWith('[')) {
    return ''
  }
  const inner = s.replace(/^['"]/, '').replace(/['"]$/, '')
  if (inner.startsWith('{') || inner.startsWith('[')) {
    return ''
  }
  return s
}

/* ═══════════════════════════════════════════
   Fallback — нераспознанный урок
   ═══════════════════════════════════════════ */

function LessonFallback({ text, topic, lessonKeys }) {
  const [showRaw, setShowRaw] = useState(false)

  return (
    <div className="lesson-card lesson-card--fallback">
      {topic && (
        <div className="lesson-card__header">
          <span className="lesson-card__icon">⚠️</span>
          <h2 className="lesson-card__title">Не удалось загрузить урок</h2>
        </div>
      )}
      <div className="lesson-card__body lesson-fallback">
        <p>{'⚠️'} Содержимое урока временно недоступно для отображения карточками.</p>
        {lessonKeys && lessonKeys.length > 0 && (
          <p style={{ fontSize: '11px', color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
            Ключи lesson: [{lessonKeys.join(', ')}]
          </p>
        )}
        {text && (
          <>
            <button
              type="button"
              className="lesson-card__raw-toggle"
              onClick={() => setShowRaw((prev) => !prev)}
            >
              {showRaw ? 'Скрыть' : 'Показать'} сырой ответ (для отладки)
            </button>
            {showRaw && (
              <pre className="lesson-card__raw-text">
                {String(text || '').substring(0, 3000)}
              </pre>
            )}
          </>
        )}
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════
   PlainLesson — связный текст без структуры
   ═══════════════════════════════════════════ */

function PlainLesson({ text, topic }) {
  const paragraphs = String(text || '').split(/\n{2,}/).filter((p) => {
    const t = p.trim()
    return t && !t.startsWith('{') && !t.startsWith('[')
  })
  return (
    <div className="lesson-card">
      <div className="lesson-card__header">
        <span className="lesson-card__icon">📚</span>
        <h2 className="lesson-card__title">{topic || 'Урок'}</h2>
      </div>
      <div className="lesson-card__body lesson-plain">
        {paragraphs.map((p, i) => (
          <p key={i}><LatexText text={p} /></p>
        ))}
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════
   Бейдж оценки
   ═══════════════════════════════════════════ */

const EVAL_LABELS = {
  structure: 'структура',
  citations: 'цитаты',
  diagram: 'схема',
  readability: 'читаемость',
  length: 'объём',
}

function EvalBadge({ evalData }) {
  if (!evalData || !evalData.criteria) return null
  const ok = evalData.verdict === 'pass'
  return (
    <div className={`lesson-eval ${ok ? 'ok' : 'warn'}`}>
      {ok ? '✅ Проверено' : '🔍 Есть что улучшить'}:
      {Object.entries(evalData.criteria).map(([k, v]) => (
        <span key={k} className="lesson-eval-crit">
          {EVAL_LABELS[k] || k}: {Math.round(v * 10)}/10
        </span>
      ))}
    </div>
  )
}

/* ═══════════════════════════════════════════
   Секция: Термины (chips/tags)
   ═══════════════════════════════════════════ */

function TermsSection({ terms }) {
  if (!terms || terms.length === 0) return null
  return (
    <div className="lesson-section lesson-section--terms">
      <div className="lesson-section__header">
        <span className="lesson-section__icon">🏷️</span>
        <h3 className="lesson-section__title">Ключевые термины</h3>
      </div>
      <div className="lesson-terms">
        {terms.map((t, i) => {
          const termText = typeof t === 'string' ? t : t.term
          const defText = typeof t === 'string' ? '' : t.definition
          return (
            <span className="lesson-term-tag" key={i} title={defText}>
              {termText}
              {defText && <span className="lesson-term-tag__def">{defText}</span>}
            </span>
          )
        })}
      </div>
    </div>
  )
}

/* ═══════════════════════════════════════════
   Секция: Определение
   ═══════════════════════════════════════════ */

function DefinitionSection({ definition }) {
  if (!definition) return null
  return (
    <div className="lesson-section lesson-section--definition">
      <div className="lesson-section__header">
        <span className="lesson-section__icon">💡</span>
        <h3 className="lesson-section__title">Определение</h3>
      </div>
      <p className="lesson-definition"><LatexText text={definition} /></p>
    </div>
  )
}

/* ═══════════════════════════════════════════
   Секция: Hook (зацепка)
   ═══════════════════════════════════════════ */

function HookSection({ hook }) {
  if (!hook) return null
  return (
    <div className="lesson-section lesson-section--hook">
      <div className="lesson-section__header">
        <span className="lesson-section__icon">🤔</span>
        <h3 className="lesson-section__title">Зацепка</h3>
      </div>
      <p className="lesson-hook"><LatexText text={hook} /></p>
    </div>
  )
}

/* ═══════════════════════════════════════════
   Секция: Основной контент
   ═══════════════════════════════════════════ */

/* Мусорные фрагменты от скраперов веб-страниц: навигация, пустые абзацы, «-->»,
   одиночные символы/цифры. Такие секции урока бесполезны ученику. */
const NOISE_RE = /^(?:\s*[-–—>|·•*×+~#]+[\s]*)+$/
const NAV_WORDS = new Set([
  'войти', 'вход', 'зарегистрироваться', 'выйти', 'главная', 'меню', 'подписаться',
  'поделиться', 'написать сообщение', 'все блоги', 'все файлы', 'все тесты', 'выбрать материалы',
  'сайты учителей', 'организационный момент', 'проверка знаний', 'объяснение материала',
  'закрепление изученного', 'итоги урока', 'рассказать о сайте', 'местоположение', 'специализация',
])
/* Промо/навигация сайтов: короткие фразы с «скидка/готовые/учитель» — не контент урока */
const PROMO_RE = /скидк|готовые (учебные|ключевые)|добро пожаловать|войти|зарегистрир|создать сайт|подписать|реклама|баннер|cookie|куки/
function isNoiseSection(heading, body) {
  const h = String(heading || '').trim().toLowerCase()
  const b = String(body || '').trim()
  if (!b) return true
  if (NOISE_RE.test(b)) return true
  if (b.length < 2 && /^[\d×xX.+-]$/.test(b)) return true
  if (b.length <= 24 && NAV_WORDS.has(b.toLowerCase())) return true
  if (PROMO_RE.test(b.toLowerCase())) return true
  if (!h && b.length < 6) return true
  return false
}

function ContentSections({ sections }) {
  if (!sections || sections.length === 0) return null
  const cleanSections = sections.filter((s) => {
    const heading = s.heading || s.title || ''
    const body = s.body || s.content || ''
    return !isNoiseSection(heading, body)
  })
  if (cleanSections.length === 0) return null
  return (
    <div className="lesson-sections">
      {cleanSections.map((s, i) => {
        const heading = s.heading || s.title || `Часть ${i + 1}`
        const body = s.body || s.content || ''
        return (
          <details className="lesson-section lesson-section--content" key={i} open={i === 0}>
            <summary className="lesson-section__header">
              <span className="lesson-section__number">{i + 1}</span>
              <h3 className="lesson-section__title"><LatexText text={heading} /></h3>
            </summary>
            <div className="lesson-section__body">
              <p className="lesson-section-body"><LatexText text={body} /></p>
              {s.citation && <div className="lesson-citation">📖 {s.citation}</div>}
              {s.check_question && (
                <div className="lesson-check">💭 Проверь себя: <LatexText text={s.check_question} /></div>
              )}
            </div>
          </details>
        )
      })}
    </div>
  )
}

/* ═══════════════════════════════════════════
   Секция: Диаграмма
   ═══════════════════════════════════════════ */

function DiagramSection({ diagram }) {
  if (!diagram) return null
  return (
    <div className="lesson-section lesson-section--diagram">
      <div className="lesson-section__header">
        <span className="lesson-section__icon">📊</span>
        <h3 className="lesson-section__title">Схема</h3>
      </div>
      <LessonDiagram diagram={diagram} />
    </div>
  )
}

/* ═══════════════════════════════════════════
   Секция: Итог
   ═══════════════════════════════════════════ */

function SummarySection({ summary }) {
  if (!summary) return null
  return (
    <div className="lesson-section lesson-section--summary">
      <div className="lesson-section__header">
        <span className="lesson-section__icon">✅</span>
        <h3 className="lesson-section__title">Итог</h3>
      </div>
      <p className="lesson-summary"><LatexText text={summary} /></p>
    </div>
  )
}

/* ═══════════════════════════════════════════
   Главный компонент LessonPanel
   ═══════════════════════════════════════════ */

export default function LessonPanel({ text, topic, lesson }) {
  /* ── Проверка на структурированный урок ── */
  const structuredCondition = lesson && typeof lesson === 'object' && !Array.isArray(lesson) && (
    (Array.isArray(lesson.sections) && lesson.sections.length > 0) ||
    lesson.definition ||
    lesson.hook ||
    (Array.isArray(lesson.key_terms) && lesson.key_terms.length > 0)
  )

  const raw = structuredCondition ? lesson : null

  /* ── Санитизация данных ── */
  const data = useMemo(() => (raw ? {
    ...raw,
    title: clean(raw.title),
    hook: clean(raw.hook),
    definition: clean(raw.definition),
    summary: clean(raw.summary),
    key_terms: (raw.key_terms || [])
      .map((t) => ({
        term: clean(typeof t === 'string' ? t : t?.term),
        definition: clean(typeof t === 'string' ? '' : t?.definition),
      }))
      .filter((t) => t.term),
    sections: (raw.sections || []).map((s) => ({
      ...s,
      heading: clean(s?.heading),
      title: clean(s?.title),
      body: clean(s?.body),
      content: clean(s?.content),
      citation: clean(s?.citation),
      check_question: clean(s?.check_question),
    })),
  } : null), [raw])

  /* ── Fallback: нет данных ── */
  if (!data) {
    const isObject = lesson && typeof lesson === 'object'
    const displayText = isObject ? '' : (text || '')
    // Связный текст без структуры (старый формат/кэш) — показываем абзацами,
    // а не прячем за debug-toggle.
    if (!isObject && displayText) {
      return <PlainLesson text={text} topic={topic} />
    }
    return <LessonFallback text={displayText} topic={topic} lessonKeys={isObject ? Object.keys(lesson) : null} />
  }

  /* ── Если вообще ничего полезного нет — plain ── */
  const hasAnyContent = data.hook || data.definition ||
    (data.key_terms && data.key_terms.length > 0) ||
    (data.sections && data.sections.length > 0) ||
    data.diagram || data.summary
  if (!hasAnyContent) {
    return <PlainLesson text={text} topic={topic} />
  }

  const title = data.title || topic || ''

  return (
    <div className="lesson-card">
      {/* Header карточки */}
      <div className="lesson-card__header">
        <span className="lesson-card__icon">📚</span>
        <div className="lesson-card__header-text">
          <h2 className="lesson-card__title">{title}</h2>
          {topic !== title && topic && <span className="lesson-card__subtitle">{topic}</span>}
        </div>
      </div>

      {/* Body карточки — секции */}
      <div className="lesson-card__body">
        <HookSection hook={data.hook} />
        <DefinitionSection definition={data.definition} />
        <TermsSection terms={data.key_terms} />
        <DiagramSection diagram={data.diagram} />
        <ContentSections sections={data.sections} />
        <SummarySection summary={data.summary} />
      </div>

      {/* Footer — оценка */}
      <EvalBadge evalData={data.eval} />
    </div>
  )
}
