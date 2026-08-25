import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

  it('секции урока — аккордеон: первая открыта, клик раскрывает остальные', async () => {
    const lesson = {
      title: 'Атмосфера',
      definition: 'Атмосфера — газовая оболочка Земли.',
      sections: [
        { heading: 'Состав', body: 'В атмосфере есть азот и кислород.' },
        { heading: 'Роль', body: 'Атмосфера защищает от радиации.' },
      ],
    }
    const { container } = render(<LessonPanel text="" topic="Атмосфера" lesson={lesson} />)

    const details = container.querySelectorAll('details.lesson-section--content')
    expect(details.length).toBe(2)
    // первая секция раскрыта по умолчанию, остальные свёрнуты
    expect(details[0].open).toBe(true)
    expect(details[1].open).toBe(false)

    // клик по заголовку второй секции раскрывает её
    await userEvent.click(screen.getByText('Роль'))
    expect(details[1].open).toBe(true)
    expect(screen.getByText(/защищает от радиации/)).toBeInTheDocument()
  })

  it('секции без тела не показываются', () => {
    const lesson = {
      title: 'Атмосфера',
      sections: [
        { heading: 'Пустая', body: '' },
        { heading: 'Состав', body: 'В атмосфере есть азот и кислород.' },
      ],
    }
    render(<LessonPanel text="" topic="Атмосфера" lesson={lesson} />)
    expect(screen.queryByText('Пустая')).not.toBeInTheDocument()
    expect(screen.getByText('Состав')).toBeInTheDocument()
  })
})
