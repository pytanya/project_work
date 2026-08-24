import { render, screen } from '@testing-library/react'
import LessonPanel from '../LessonPanel'

describe('LessonPanel', () => {
  it('рендерит структурированный урок карточками (hook/термины/секции/итог)', () => {
    const lesson = {
      title: 'Теплые течения',
      hook: 'Почему в Норвегии зимой не замерзают порты?',
      definition: 'Тёплые течения — потоки воды из тропиков к полюсам.',
      key_terms: [
        { term: 'Гольфстрим', definition: 'тёплое течение в Атлантике' },
      ],
      sections: [
        {
          heading: 'Как образуются',
          body: 'Их создают ветры и вращение Земли.',
          citation: '§12',
          check_question: 'Назови главный пример тёплого течения.',
        },
      ],
      summary: 'Тёплые течения смягчают климат прибрежных регионов.',
    }
    render(<LessonPanel text="полный текст" topic="Теплые течения" lesson={lesson} />)

    expect(screen.getByText('📖 Урок: Теплые течения')).toBeInTheDocument()
    expect(screen.getByText(/Почему в Норвегии зимой не замерзают порты/)).toBeInTheDocument()
    expect(screen.getByText(/Гольфстрим/)).toBeInTheDocument()
    expect(screen.getByText(/Как образуются/)).toBeInTheDocument()
    expect(screen.getByText(/§12/)).toBeInTheDocument()
    expect(screen.getByText(/Проверь себя: Назови главный пример/)).toBeInTheDocument()
    expect(screen.getByText(/смягчают климат/)).toBeInTheDocument()
  })

  it('рендерит бейдж качества урока (детерминированный судья-lite)', () => {
    const lesson = {
      title: 'Атмосфера',
      hook: 'Почему небо голубое?',
      definition: 'Атмосфера — газовая оболочка.',
      sections: [
        { heading: 'Состав', body: 'Азот и кислород — основа.', citation: '§12' },
      ],
      summary: 'Итог.',
      eval: {
        verdict: 'pass',
        avg_score: 0.9,
        criteria: { structure: 1, citations: 1, diagram: 1, readability: 1, length: 0.9 },
      },
    }
    render(<LessonPanel text="текст" topic="Атмосфера" lesson={lesson} />)
    expect(screen.getByText(/Проверено/)).toBeInTheDocument()
    expect(screen.getByText(/структура: 10\/10/)).toBeInTheDocument()
    expect(screen.getByText(/цитаты: 10\/10/)).toBeInTheDocument()
  })

  it('рендерит обычный текст, если структуры нет (explain/deep_dive)', () => {
    render(<LessonPanel text="Абзац 1.\n\nАбзац 2." topic="Атмосфера" />)
    expect(screen.getByText('📖 Урок: Атмосфера')).toBeInTheDocument()
    expect(screen.getByText(/Абзац 1/)).toBeInTheDocument()
    expect(screen.getByText(/Абзац 2/)).toBeInTheDocument()
  })
})
