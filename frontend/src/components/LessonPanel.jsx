// LessonPanel — урок по теме (режим lesson): связный текст, без маркдауна
import LatexText from './LatexText'

export default function LessonPanel({ text, topic }) {
  const paragraphs = String(text || '').split(/\n{2,}/)
  return (
    <div className="lesson">
      {topic && <div className="lesson-topic">📖 Урок: {topic}</div>}
      {paragraphs.map((p, i) => (
        <p key={i}><LatexText text={p} /></p>
      ))}
    </div>
  )
}
