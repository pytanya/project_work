import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'
import { api } from '../api'

describe('App', () => {
  beforeEach(() => {
    vi.spyOn(api, 'createSession').mockResolvedValue({ session_id: 'sess-1' })
    vi.spyOn(api, 'intakeStatus').mockResolvedValue({
      missing_fields: ['learner_type'],
      next_question: 'Для кого готовим материал — ученик какого класса или студент?',
      complete: false,
    })
    vi.spyOn(api, 'postIntake').mockResolvedValue({ missing_fields: [], complete: true })
    vi.spyOn(api, 'postMessage').mockResolvedValue({ type: 'system', payload: {} })
    vi.spyOn(api, 'uploadFile').mockResolvedValue({ ok: true, filename: 'b.pdf', status: 'ready' })
    vi.spyOn(api, 'findTextbook').mockResolvedValue({ status: 'ready' })
    vi.spyOn(api, 'getSession').mockResolvedValue({
      current_question: null,
      intake_field: null,
      agent_question: null,
      knowledge_map: {},
      correct_count: 0,
      answered_count: 0,
      source_status: null,
    })
    vi.spyOn(api, 'getGraph').mockResolvedValue({ nodes: [], edges: [] })
    vi.spyOn(api, 'deleteSession').mockResolvedValue({ ok: true })
    vi.spyOn(api, 'sourceStatus').mockResolvedValue({ status: 'ready' })
    vi.spyOn(api, 'history').mockResolvedValue({ sessions: [] })
    vi.spyOn(api, 'getSourcePolicy').mockResolvedValue({ allow_any_sources: true, whitelist: [] })
    vi.spyOn(api, 'putSourcePolicy').mockResolvedValue({ allow_any_sources: true, whitelist: [] })

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ allow_any_sources: true, whitelist: [] }),
    })
  })

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
        data: { error: 'Файл повреждён' },
      }),
    })

    const errs = await screen.findAllByText(/Файл повреждён/)
    expect(errs.length).toBeGreaterThan(0)
  })

  it('source.progress сбрасывает баннер чек-листа', async () => {
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    ws.onmessage({
      data: JSON.stringify({
        event: 'source.progress',
        data: { phase: 'reading', progress: 50, message: 'Читаем PDF...' },
      }),
    })

    await waitFor(() => {
      expect(screen.queryByText(/Для кого готовим материал/)).not.toBeInTheDocument()
    })
    const steps = await screen.findAllByText(/Читаем PDF\.\.\./)
    expect(steps.length).toBeGreaterThan(0)
  })

  it('tutor.lesson превращает стрим-пузырь в урок (без дубля и каретки)', async () => {
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]

    ws.onmessage({
      data: JSON.stringify({
        event: 'tutor.stream_chunk',
        data: { chunk: 'Начинаем урок по географии. ' },
      }),
    })

    ws.onmessage({
      data: JSON.stringify({
        event: 'tutor.lesson',
        data: {
          topic: 'Гидросфера',
          lesson_text: '# Гидросфера\n\nВодная оболочка Земли.',
          lesson: {
            title: 'Гидросфера',
            hook: 'Вода покрывает большую часть планеты',
            definition: 'Водная оболочка Земли',
            key_terms: [{ term: 'Мировой океан', definition: 'Главная часть гидросферы' }],
            sections: [{ heading: 'Состав', body: 'Океаны, моря, реки, озера' }],
          },
        },
      }),
    })

    await waitFor(() => {
      expect(screen.getByText('Гидросфера')).toBeInTheDocument()
    })
  })

  it('системное событие, прервавшее поток токенов, убирает мигающую каретку', async () => {
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]

    ws.onmessage({
      data: JSON.stringify({
        event: 'token',
        data: { text: 'Обдумываю ответ… ' },
      }),
    })
    await waitFor(() => {
      expect(document.querySelector('.stream-caret')).toBeInTheDocument()
    })

    // system без финализации (agent.message / heartbeat / источник) обрывает поток
    ws.onmessage({
      data: JSON.stringify({
        event: 'system',
        data: { kind: 'agent.message', message: 'Готово, вот ответ.' },
      }),
    })

    await waitFor(() => {
      expect(document.querySelector('.stream-caret')).not.toBeInTheDocument()
    })
  })
})
