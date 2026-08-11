// ExplanationPanel — объяснение ошибки с цитатой §N (раздел 9.2)
export default function ExplanationPanel({ text, citation }) {
  return (
    <div className="explanation">
      <div className="explanation-text">{text}</div>
      {citation && (citation.paragraph || citation.source) && (
        <div className="citation">
          Источник: {citation.paragraph}
          {citation.source ? ` — ${citation.source}` : ''}
        </div>
      )}
    </div>
  )
}
