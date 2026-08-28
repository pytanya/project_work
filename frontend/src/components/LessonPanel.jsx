// LessonPanel — урок по теме (режим lesson).
// Поддержка: структурированный Lesson (карточки) ИЛИ plain/markdown текст (новый стриминг).
import { useMemo, useState } from 'react'
import LatexText from './LatexText'
import LessonDiagram from './LessonDiagram'

/* ═══════════════════════════════════════════
   Утилиты санитизации
   ═══════════════════════════════════════════ */

/* Нормализованные ключи секций урока (tutor._normalize_heading) → русские подписи.
   Модель иногда присылает заголовки секций как content/summary/check/terms —
   в UI показываем осмысленное русское название. */
const SECTION_LABELS = {
  content: 'Подробное объяснение',
  definition: 'Определение',
  terms: 'Основные понятия',
  key_terms: 'Ключевые понятия',
  examples: 'Примеры',
  check: 'Проверь себя',
  summary: 'Краткий итог',
  hook: 'Введение',
  intro: 'Введение',
  introduction: 'Введение',
  diagram: 'Схема',
  'check yourself': 'Проверь себя',
  'key terms': 'Ключевые понятия',
  eval: 'Оценка',
}

/** Читаемый заголовок секции: русская подпись для известных ключей, иначе — как есть. */
function sectionLabel(heading) {
  const h = String(heading || '').trim()
  if (!h) return ''
  return SECTION_LABELS[h.toLowerCase()] || h
}

/** Отфильтровывает JSON-подобные значения от модели, чтобы в UI не появлялся сырой JSON */
function clean(v, field) {
  if (field === undefined && typeof v === 'string') field = 'unknown'
  const s = String(v ?? '').trim().replace(/^\ufeff/, '')
  if (!s) return ''
  if (s.startsWith('{') || s.startsWith('[')) {
    return ''
  }
  const inner = s.replace(/^['"]/g, '').replace(/['"]$/g, '')
  if (inner.startsWith('{') || inner.startsWith('[')) {
    return ''
  }
  return s
}

/* ═══════════════════════════════════════════
   Markdown → Paragraphs парсер
   ═══════════════════════════════════════════ */

/** Парсит markdown текст на секции по заголовкам ## . Возвращает массив {heading, body}. */
function parseMarkdownSections(mdText) {
  if (!mdText || typeof mdText !== 'string') return []
  
  const lines = mdText.split('\n')
  const sections = []
  let currentHeading = null
  let currentBody = []
  
  for (const line of lines) {
    const m = line.match(/^##+\s+(.+)$/)
    if (m) {
      // Сохраняем предыдущую секцию
      if (currentHeading && currentBody.length > 0) {
        sections.push({
          heading: currentHeading,
          body: currentBody.join('\n').trim()
        })
      }
      currentHeading = m[1].trim()
      currentBody = []
    } else if (line.trim() === '') {
      // Пустая строка — не сбрасываем, но можем пропускать
      continue
    } else {
      currentBody.push(line)
    }
  }
  
  // Последняя секция
  if (currentHeading && currentBody.length > 0) {
    sections.push({
      heading: currentHeading,
      body: currentBody.join('\n').trim()
    })
  }
  
  return sections
}

/** Извлекает определение (текст после "## Определение" или первый значимый абзац). */
function extractDefinition(mdText) {
  if (!mdText) return ''
  const defMatch = mdText.match(/^##\s*Определение\s*\n([\s\S]*?)(?=^##|$)/m)
  if (defMatch) {
    return defMatch[1].trim().split('\n').filter(l => l.trim()).join(' ').trim()
  }
  // Fallback: первый значимый абзац
  const paragraphs = mdText.split(/\n{2,}/).filter(p => p.trim().length > 50)
  return paragraphs[0]?.trim() || ''
}

/** Извлекает термины из маркированного списка после "## Основные понятия". */
function extractTerms(mdText) {
  if (!mdText) return []
  const termsMatch = mdText.match(/^##\s*[Оо]сновные\s*[Ппонятия]\s*\n([\s\S]*?)(?=^##|$)/m)
  if (!termsMatch) return []
  
  const lines = termsMatch[1].split('\n')
  const terms = []
  for (const line of lines) {
    const trimmed = line.trim()
    // Формат: **-термин**: определение
    const m = trimmed.match(/^[-*]\s*\*\*(.+?)\*\*\s*[:：]\s*(.+)/)
    if (m) {
      terms.push({ term: m[1].trim(), definition: m[2].trim() })
    } else {
      const m2 = trimmed.match(/^[-*]\s+(.+?)[:：]\s*(.+)/)
      if (m2) {
        terms.push({ term: m2[1].trim(), definition: m2[2].trim() })
      }
    }
  }
  return terms.slice(0, 10)
}

/** Извлекает вопрос для самопроверки. */
function extractCheckQuestion(mdText) {
  if (!mdText) return ''
  const checkMatch = mdText.match(/^##\s*[Пп]роверь\s*себя\s*\n([\s\S]*?)(?=^##|$)/m)
  if (checkMatch) {
    return checkMatch[1].trim().split('\n').filter(l => l.trim()).join(' ').trim()
  }
  return ''
}

/** Извлекает итог. */
function extractSummary(mdText) {
  if (!mdText) return ''
  const sumMatch = mdText.match(/^##\s*(?:Краткий\s+Итог|Заключение|Итого)\s*\n([\s\S]*?)(?=^##|$)/m)
  if (sumMatch) {
    return sumMatch[1].trim().split('\n').filter(l => l.trim()).join(' ').trim()
  }
  return ''
}

/* ═══════════════════════════════════════════
   Markdown Inline-парсер для контента секций
   ═══════════════════════════════════════════ */

/** Экранирует специальные HTML-символы */
function escapeHtml(str) {
  const A = String.fromCharCode(38) + 'amp;'
  const L = String.fromCharCode(38) + 'lt;'
  const G = String.fromCharCode(38) + 'gt;'
  return str
.replace(/&/gu, A)
.replace(/</gu, L)
.replace(/>/gu, G)
}

function renderMarkdownLine(line) {
  let html = String(line || '')
  
  // Извлекаем markdown-изображения ![alt](url) ДО экранирования
  // (иначе URL может содержать &lt; или другие экранированные символы)
  const imagePlaceholders = []
  html = html.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match, alt, url) => {
    const placeholder = `__IMG_${imagePlaceholders.length}__`
    imagePlaceholders.push(`<img src="${url}" alt="${alt}" loading="lazy" class="lesson-image" />`)
    return placeholder
  })
  
  // Экранируем HTML (порядок важен: & первым!)
  html = escapeHtml(html)
  
  // Жирный текст: **текст** или __текст__
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/__(.+?)__/g, '<strong>$1</strong>')
  
  // Курсив: *текст* или _текст_
  html = html.replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/(?<!\w)_(.+?)_(?!\w)/g, '<em>$1</em>')
  
  // Код: `текст`
  html = html.replace(/`(.+?)`/g, '<code>$1</code>')
  
  // Восстанавливаем изображения
  for (let i = 0; i < imagePlaceholders.length; i++) {
    html = html.replace(`__IMG_${i}__`, imagePlaceholders[i])
  }
  
  return html
}

/** Парсит блок текста и возвращает массив React-фрагментов (абзацы, списки). */
function parseInlineMarkdown(text) {
  if (!text) return null
  
  const lines = String(text).split('\n')
  const fragments = []
  let inList = false
  let listItems = []
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trim()
    
    // Маркированный список
    const bulletMatch = trimmed.match(/^[-*•]\s+(.+)$/)
    if (bulletMatch) {
      if (!inList) {
        inList = true
        listItems = []
      }
      listItems.push(renderMarkdownLine(bulletMatch[1]))
      continue
    }
    
    // Завершаем список если строка не является элементом списка
    if (inList) {
      fragments.push(<ul key={`list-${fragments.length}`}>{listItems.map((item, j) => (
        <li key={j}><span dangerouslySetInnerHTML={{ __html: item }} /></li>
      ))}</ul>)
      inList = false
      listItems = []
    }
    
    // Горизонтальный разделитель
    if (/^[-*_]{3,}$/.test(trimmed)) {
      fragments.push(<hr key={`hr-${fragments.length}`} />)
      continue
    }
    
    // Обычный текст — добавляем как абзац
    if (trimmed) {
      fragments.push(<p key={`p-${fragments.length}`}><span dangerouslySetInnerHTML={{ __html: renderMarkdownLine(trimmed) }} /></p>)
    }
  }
  
  // Завершаем остаток списка
  if (inList && listItems.length > 0) {
    fragments.push(<ul key={`list-${fragments.length}`}>{listItems.map((item, j) => (
      <li key={j}><span dangerouslySetInnerHTML={{ __html: item }} /></li>
    ))}</ul>)
  }
  
  return fragments.length === 0 ? null : fragments
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
   MarkdownLesson — рендеринг из raw_text (markdown)
   ═══════════════════════════════════════════ */

function MarkdownLesson({ rawText, topic, lesson }) {
  const sections = useMemo(() => parseMarkdownSections(rawText), [rawText])
  const definition = useMemo(() => extractDefinition(rawText), [rawText])
  const terms = useMemo(() => extractTerms(rawText), [rawText])
  const checkQuestion = useMemo(() => extractCheckQuestion(rawText), [rawText])
  const summary = useMemo(() => extractSummary(rawText), [rawText])
  
  // Title из первого заголовка # или lesson.title
  const title = useMemo(() => {
    const firstLineH = rawText?.match(/^#\s+(.+)$/m)?.[1]?.trim()
    return lesson?.title || firstLineH || topic || 'Урок'
  }, [rawText, lesson?.title, topic])
  
  return (
    <div className="lesson-card">
      {/* Header */}
      <div className="lesson-card__header">
        <span className="lesson-card__icon">📚</span>
        <div className="lesson-card__header-text">
          <h2 className="lesson-card__title">{title}</h2>
          {topic !== title && topic && <span className="lesson-card__subtitle">{topic}</span>}
        </div>
      </div>
      
      <div className="lesson-card__body">
        <HookSection hook={definition ? `💡 ${definition.substring(0, 100)}${definition.length > 100 ? '...' : ''}` : ''} />
        <DefinitionSection definition={definition} />
        <TermsSection terms={terms.length > 0 ? terms : (lesson?.key_terms || [])} />
        {sections.length > 0 && (
          <ContentSections sections={sections.map(s => ({
            heading: s.heading,
            body: s.body,
            citation: lesson?.sections?.[0]?.citation || '',
            check_question: ''
          }))} />
        )}
        {checkQuestion && (
          <div className="lesson-section lesson-section--check">
            <div className="lesson-section__header">
              <span className="lesson-section__icon">💭</span>
              <h3 className="lesson-section__title">Проверь себя</h3>
            </div>
            <p className="lesson-check"><LatexText text={checkQuestion} /></p>
          </div>
        )}
        <SummarySection summary={summary || lesson?.summary || ''} />
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
        const heading = sectionLabel(s.heading || s.title) || `Часть ${i + 1}`
        const body = s.body || s.content || ''
        return (
          <details className="lesson-section lesson-section--content" key={i} open={i === 0}>
            <summary className="lesson-section__header">
              <span className="lesson-section__number">{i + 1}</span>
              <h3 className="lesson-section__title"><LatexText text={heading} /></h3>
            </summary>
            <div className="lesson-section__body">
              {parseInlineMarkdown(body) || (
                <p className="lesson-section-body"><LatexText text={body} /></p>
              )}
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
  /* ═ Hooks вызываем всегда в одинаковом порядке, ДО любых return = */
  
  /* ── Санитизация структурированного урока (всегда вызывается) ── */
  const data = useMemo(() => {
    const isStructured = lesson && typeof lesson === 'object' && !Array.isArray(lesson) && (
      (Array.isArray(lesson.sections) && lesson.sections.length > 0) ||
      lesson.definition ||
      lesson.hook ||
      (Array.isArray(lesson.key_terms) && lesson.key_terms.length > 0)
    )
    if (!isStructured) return null
    
    const raw = lesson
    return {
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
    }
  }, [lesson])

  /* ── Путь 1: markdown-стриминг (raw_text) ── */
  const rawMd = lesson?.raw_text || ''
  if (rawMd && typeof rawMd === 'string' && rawMd.trim().length > 50) {
    return <MarkdownLesson rawText={rawMd} topic={topic} lesson={lesson} />
  }

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
