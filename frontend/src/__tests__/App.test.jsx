import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'
import { api } from '../api'

vi.mock('../api', async () => {
  const actual = await vi.importActual('../api')
  return {
    ...actual,
    api: {
      createSession: vi.fn().mockResolvedValue({ session_id: 'sess-1' }),
      intakeStatus: vi.fn().mockResolvedValue({
        missing_fields: ['learner_type'],
        next_question: 'Для кого готовим материал — ученик какого класса или студент?',
        complete: false,
      }),
      postIntake: vi.fn().mockResolvedValue({ missing_fields: [], complete: true }),
      postMessage: vi.fn().mockResolvedValue({ type: 'system', payload: {} }),
      uploadFile: vi.fn().mockResolvedValue({ ok: true, filename: 'b.pdf', status: 'ready' }),
      findTextbook: vi.fn().mockResolvedValue({ status: 'ready' }),
      getSession: vi.fn().mockResolvedValue({
        current_question: null,
        intake_field: null,
        agent_question: null,
        knowledge_map: {},
        correct_count: 0,
        answered_count: 0,
        source_status: null,
      }),
      deleteSession: actual.api.deleteSession,
      sourceStatus: actual.api.sourceStatus,
      history: actual.api.history,
    },
    wsUrl: vi.fn(() => 'ws://fake/ws'),
  }
})

describe('App', () => {
  it('показывает «Готовим занятие…», затем приходит вопрос чек-листа', async () => {
    render(<App />)
    expect(screen.getByText(/Готовим занятие/)).toBeInTheDocument()

    // вопрос показывается дважды: в ленте и в карточке IntakeWizard
    const questions = await screen.findAllByText(/Для кого готовим материал/)
    expect(questions.length).toBeGreaterThan(0)
    expect(screen.getByPlaceholderText('Ваш ответ…')).toBeEnabled()
  })

  it('ответ в чек-лист вызывает postIntake', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    await user.type(screen.getByPlaceholderText('Ваш ответ…'), 'студент')
    await user.click(screen.getByRole('button', { name: 'Отправить' }))

    await waitFor(() => expect(api.postIntake).toHaveBeenCalledWith('sess-1', 'студент'))
  })

  it('WS-событие quiz.card рисует карточку вопроса', async () => {
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    ws.onmessage({
      data: JSON.stringify({
        event: 'quiz.card',
        data: {
          question: 'Что такое атмосфера?',
          options: ['А', 'Б'],
          answer_type: 'single',
          topic: 'Атмосфера',
          difficulty: 'easy',
          question_id: 'q1',
        },
      }),
    })

    const qs = await screen.findAllByText('Что такое атмосфера?')
    expect(qs.length).toBeGreaterThan(0)
    expect(screen.getByText('А')).toBeInTheDocument()
  })

  it('WS-событие source.failed выводит ошибку', async () => {
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    ws.onmessage({
      data: JSON.stringify({
        event: 'source.failed',
        data: { reason: 'empty_result', message: 'Материалы не найдены' },
      }),
    })

    expect(await screen.findByText('Материалы не найдены')).toBeInTheDocument()
  })

  it('source.progress сбрасывает баннер чек-листа', async () => {
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)
    expect(screen.getByText(/Чек-лист/)).toBeInTheDocument()

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    ws.onmessage({
      data: JSON.stringify({
        event: 'source.progress',
        data: { stage: 'catalog', status: 'searching', message: 'Поиск материалов по теме…' },
      }),
    })

    expect(await screen.findByText('Поиск материалов по теме…')).toBeInTheDocument()
    // баннер чек-листа должен исчезнуть (фаза источника = intake завершён)
    await waitFor(() => expect(screen.queryByText(/Чек-лист/)).not.toBeInTheDocument())
  })

  it('tutor.lesson превращает стрим-пузырь в урок (без дубля и каретки)', async () => {
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    ws.onmessage({ data: JSON.stringify({ event: 'token', data: { text: 'Атмосфера' } }) })
    ws.onmessage({ data: JSON.stringify({ event: 'token', data: { text: ' — газовая оболочка.' } }) })
    ws.onmessage({
      data: JSON.stringify({
        event: 'tutor.lesson',
        data: { text: 'Атмосфера — газовая оболочка.', topic: 'Атмосфера' },
      }),
    })

    // пузырь стал уроком: заголовок темы от LessonPanel виден
    expect(await screen.findByText('📖 Урок: Атмосфера')).toBeInTheDocument()
    // стрим-каретка исчезла — «живой» пузырь не остался висеть с мигающим курсором
    expect(document.querySelector('.stream-caret')).not.toBeInTheDocument()
    // дубля нет: текст урока в ленте ровно один
    expect(screen.getAllByText('Атмосфера — газовая оболочка.')).toHaveLength(1)
  })
})
