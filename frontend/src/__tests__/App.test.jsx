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

  it('quiz.hint не сносит карточку вопроса и рисует подсказку внутри неё', async () => {
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    const quizCard = () => ({
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
    ws.onmessage(quizCard())
    await screen.findByText('Что такое атмосфера?')

    ws.onmessage({
      data: JSON.stringify({
        event: 'quiz.hint',
        data: { question_id: 'q1', hint: 'Подумай про газовый состав.', level: 1, attempts_left: 1 },
      }),
    })

    await waitFor(() => {
      // карточка осталась на месте
      expect(screen.getByText('Что такое атмосфера?')).toBeInTheDocument()
      // подсказка — внутри карточки, а не отдельным пузырём в ленте
      expect(screen.getByText('Подумай про газовый состав.')).toBeInTheDocument()
    })
    expect(document.querySelector('.quiz-hintbox')).toBeInTheDocument()
    expect(document.querySelector('.bubble.hint')).not.toBeInTheDocument()
    // лента НЕ получила подсказку отдельным сообщением (не раздвигается)
    expect(screen.queryByText(/Подсказка 1/)).toBeTruthy()
  })

  it('информационное system-событие (source.cached) не сносит карточку квиза', async () => {
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    ws.onmessage({
      data: JSON.stringify({
        event: 'quiz.card',
        data: {
          question: 'Какой газ преобладает в атмосфере?',
          options: ['Кислород', 'Азот'],
          answer_type: 'single',
          topic: 'Атмосфера',
          difficulty: 'easy',
          question_id: 'q2',
        },
      }),
    })
    await screen.findByText('Какой газ преобладает в атмосфере?')

    // фоновое событие от reuse-гейта при запросе подсказки (не результат ответа)
    ws.onmessage({
      data: JSON.stringify({
        event: 'system',
        data: { kind: 'source.cached', message: 'Кэшированные материалы: 1 источников' },
      }),
    })

    await waitFor(() => {
      expect(screen.getByText('Какой газ преобладает в атмосфере?')).toBeInTheDocument()
      expect(screen.getByText('Кэшированные материалы: 1 источников')).toBeInTheDocument()
    })
  })

  it('system-фидбек с correct_count снимает карточку квиза после ответа', async () => {
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    ws.onmessage({
      data: JSON.stringify({
        event: 'quiz.card',
        data: {
          question: 'Сколько кислорода в воздухе?',
          options: ['21%', '78%'],
          answer_type: 'single',
          topic: 'Атмосфера',
          difficulty: 'easy',
          question_id: 'q3',
        },
      }),
    })
    await screen.findByText('Сколько кислорода в воздухе?')

    // событие результата ответа («Верно!») — карточка должна уйти
    ws.onmessage({
      data: JSON.stringify({
        event: 'system',
        data: { message: 'Верно!', correct_count: 1, answered_count: 1 },
      }),
    })

    await waitFor(() => {
      expect(screen.queryByText('Сколько кислорода в воздухе?')).not.toBeInTheDocument()
      expect(screen.getByText('Верно!')).toBeInTheDocument()
    })
  })

  it('клик подсказки не показывает фазовый индикатор с мусорным текстом', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    ws.onmessage({
      data: JSON.stringify({
        event: 'quiz.card',
        data: {
          question: 'Почему крупинка движется?',
          options: ['Из-за молекул', 'Сама по себе'],
          answer_type: 'single',
          topic: 'Физика',
          difficulty: 'easy',
          question_id: 'q1',
        },
      }),
    })
    await screen.findByText('Почему крупинка движется?')

    // клик по «Подсказка» → busy включился, вопрос должен остаться
    await user.click(screen.getByRole('button', { name: '💡 Подсказка' }))

    // событие source.progress (фоновый/ошибочный вход) НЕ должно создавать
    // фазовый индикатор `.has-phase` с дублирующим текстом
    ws.onmessage({
      data: JSON.stringify({
        event: 'source.progress',
        data: { stage: 'content', status: 'generating', message: 'Генерирую урок по теме…' },
      }),
    })
    await waitFor(() => {
      expect(document.querySelectorAll('.bubble.agent.progress.has-phase').length).toBe(0)
      expect(document.querySelector('.source-progress-card')).toBeInTheDocument()
      // текст шага только в карточке прогресса, не дублируется нижним индикатором
      const chatDup = [...document.querySelectorAll('.chatstream .source-progress-card__step-text')]
        .filter((el) => /Генерирую урок по теме/.test(el.textContent))
      expect(chatDup.length).toBe(1)
      expect(screen.getByText('Почему крупинка движется?')).toBeInTheDocument()
    })

    // подсказка пришла — карточка на месте, фазового мусора нет
    ws.onmessage({
      data: JSON.stringify({
        event: 'quiz.hint',
        data: { question_id: 'q1', hint: 'Вспомни про молекулы.', level: 1, attempts_left: 1 },
      }),
    })
    await waitFor(() => {
      expect(screen.getByText('Вспомни про молекулы.')).toBeInTheDocument()
      expect(document.querySelectorAll('.bubble.agent.progress.has-phase').length).toBe(0)
      expect(screen.getByText('Почему крупинка движется?')).toBeInTheDocument()
    })
  })

  it('клик варианта reuse-вопроса пишет выбор в ленту чата', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    ws.onmessage({
      data: JSON.stringify({
        event: 'intake.question',
        data: {
          question: 'Использовать уже разобранные материалы или найти другие?',
          missing_fields: ['reuse'],
          options: ['Да, использовать', 'Нет, найти другие'],
        },
      }),
    })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Да, использовать' })).toBeInTheDocument()
    })

    await user.click(screen.getByRole('button', { name: 'Да, использовать' }))

    await waitFor(() => {
      expect(api.postIntake).toHaveBeenCalledWith('sess-1', 'Да, использовать')
    })
    // выбор зафиксирован пользовательским пузырём в ленте (протокол):
    // текст есть и в кнопке, и в новом пузыре
    const userBubbles = [...document.querySelectorAll('.bubble.user')].map((b) => b.textContent)
    expect(userBubbles).toContain('Да, использовать')
  })

  it('повторный одинаковый выбор пользователя тоже фиксируется в ленте', async () => {
    const user = userEvent.setup()
    render(<App />)
    await screen.findAllByText(/Для кого готовим материал/)

    const ws = globalThis.WebSocket.instances[globalThis.WebSocket.instances.length - 1]
    ws.onmessage({
      data: JSON.stringify({
        event: 'quiz.card',
        data: {
          question: 'Что такое броуновское движение?',
          options: ['Движение молекул в жидкости'],
          answer_type: 'single',
          topic: 'Физика',
          difficulty: 'easy',
          question_id: 'q1',
        },
      }),
    })
    await screen.findByText('Что такое броуновское движение?')

    const option = screen.getByRole('button', { name: /Движение молекул в жидкости/ })
    await user.click(option)
    await user.click(option)

    const userBubbles = [...document.querySelectorAll('.bubble.user')].map((b) => b.textContent)
    expect(userBubbles.filter((t) => t === 'Движение молекул в жидкости').length).toBe(2)
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
