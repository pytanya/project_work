// FileUpload — drag & drop PDF/DOCX/TXT (раздел 9.2)
import { useState } from 'react'

export default function FileUpload({ onUpload }) {
  const [drag, setDrag] = useState(false)

  const handleFiles = (files) => {
    const file = files && files[0]
    if (file) onUpload(file)
  }

  return (
    <div
      className={`card upload ${drag ? 'dragging' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true) }}
      onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); handleFiles(e.dataTransfer.files) }}
    >
      <h3>Загрузить учебник</h3>
      <label className="upload-label">
        Перетащите PDF/DOCX сюда или{' '}
        <span className="link">выберите файл</span>
        <input
          type="file"
          accept=".pdf,.docx,.txt"
          onChange={(e) => handleFiles(e.target.files)}
          hidden
        />
      </label>
    </div>
  )
}
