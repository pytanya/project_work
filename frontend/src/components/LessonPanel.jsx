// LessonPanel — урок по теме (режим lesson).
// Структурированный урок (LessonSchema): зацепка → определение → термины → секции
// с «Проверь себя» → итог. Если структуры нет (explain/deep_dive) — связный текст.
import { useMemo } from 'react'
import LatexText from './LatexText'
import LessonDiagram from './LessonDiagram'

// Страховка: модель иногда вкладывает весь JSON урока строкой в одно из полей.
// Такие значения не рендерим — иначе в UI вместо карточек появится сырой JSON.
function clean(v) {
  const s = String(v ?? '').trim().replace(/^\ufeff/, '')
  if (!s) return ''
  if (s.startsWith('{') || s.startsWith('[')) return ''
  const inner = s.replace(/^['"]/, '').replace(/['"]$/, '')
  if (inner.startsWith('{') || inner.startsWith('[')) return ''
  return s
}

function PlainLesson({ text, topic }) {
  const paragraphs = String(text || '').split(/\n{2,}/).filter((p) => {
    const t = p.trim()
    return t && !t.startsWith('{') && !t.startsWith('[')
  })
  return (
    <div className="lesson">
      {topic && <div className="lesson-topic">📖 Урок: {topic}</div>}
      {paragraphs.map((p, i) => (
        <p key={i}><LatexText text={p} /></p>
      ))}
    </div>
  )
}

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
      {ok ? 'Проверено' : 'Есть что улучшить'}:
      {Object.entries(evalData.criteria).map(([k, v]) => (
        <span key={k} className="lesson-eval-crit">
          {EVAL_LABELS[k] || k}: {Math.round(v * 10)}/10
        </span>
      ))}
    </div>
  )
}

export default function LessonPanel({ text, topic, lesson }) {
  const raw = lesson && Array.isArray(lesson.sections) && lesson.sections.length > 0 ? lesson : null
  // Санитизация полей: JSON-подобные значения (вложенный объект от модели) не рендерим
  const data = useMemo(() => (raw ? {
    ...raw,
    title: clean(raw.title),
    hook: clean(raw.hook),
    definition: clean(raw.definition),
    summary: clean(raw.summary),
    key_terms: (raw.key_terms || [])
      .map((t) => ({ term: clean(t?.term), definition: clean(t?.definition) }))
      .filter((t) => t.term),
    sections: (raw.sections || []).map((s) => ({
      ...s,
      heading: clean(s?.heading),
      body: clean(s?.body),
      citation: clean(s?.citation),
      check_question: clean(s?.check_question),
    })),
  } : null), [raw])
  if (!data) return <PlainLesson text={text} topic={topic} />

  const title = data.title || topic || ''
  return (
    <div className="lesson">
      {title && <div className="lesson-topic">📖 Урок: {title}</div>}
      {data.hook && (
        <div className="lesson-hook">🤔 <LatexText text={data.hook} /></div>
      )}
      {data.definition && (
        <div className="lesson-definition">📌 <LatexText text={data.definition} /></div>
      )}
      {Array.isArray(data.key_terms) && data.key_terms.length > 0 && (
        <div className="lesson-terms">
          <div className="lesson-terms-label">Словарик</div>
          {data.key_terms.map((t, i) => (
            <div className="lesson-term" key={i}>
              <strong>{t.term}</strong> — {t.definition}
            </div>
          ))}
        </div>
      )}
      {data.diagram && <LessonDiagram diagram={data.diagram} />}
      {data.sections.map((s, i) => (
        <div className="lesson-section" key={i}>
          {s.heading && <h4 className="lesson-section-heading"><LatexText text={s.heading} /></h4>}
          <p className="lesson-section-body"><LatexText text={s.body} /></p>
          {s.citation && <div className="lesson-citation">📖 {s.citation}</div>}
          {s.check_question && (
            <div className="lesson-check">💭 Проверь себя: {s.check_question}</div>
          )}
        </div>
      ))}
      {data.summary && <div className="lesson-summary">✅ <LatexText text={data.summary} /></div>}
      <EvalBadge evalData={data.eval} />
    </div>
  )
}
