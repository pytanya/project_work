// ExplanationPanel — объяснение ошибки с цитатой §N (раздел 9.2)
import LatexText from './LatexText'

export default function ExplanationPanel({ text, citation }) {
  return (
    <div className="explanation">
      <div className="explanation-text"><LatexText text={text} /></div>
      {citation && (citation.paragraph || citation.source) && (
        <div className="citation">
          Источник: {citation.paragraph}
          {citation.source ? ` — ${citation.source}` : ''}
        </div>
      )}
    </div>
  )
}
