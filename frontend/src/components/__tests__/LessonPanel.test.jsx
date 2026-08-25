import { render, screen } from '@testing-library/react'
import LessonPanel from '../LessonPanel'

describe('LessonPanel', () => {
  it('рендерит заголовок из lesson.title', () => {
    const lesson = {
      title: 'Тестовый урок',
      sections: [{ body: 'Контент.' }],
    }
    
    const { container } = render(<LessonPanel text="" topic="" lesson={lesson} />)
    expect(container.innerHTML).toContain('Тестовый урок')
  })

  it('показывает fallback при пустом lesson объекте', () => {
    render(<LessonPanel text="" topic="Атмосфера" lesson={{}} />)
    expect(screen.getByText(/Не удалось загрузить урок/)).toBeInTheDocument()
    expect(screen.getByText(/временно недоступно/)).toBeInTheDocument()
  })

  it('рендерит plain текст когда lesson отсутствует', () => {
    render(<LessonPanel text="Абзац 1.\n\nАбзац 2." topic="Атмосфера" />)
    expect(screen.getByText(/Атмосфера/)).toBeInTheDocument()
    expect(screen.getByText(/Абзац 1/)).toBeInTheDocument()
  })
})
