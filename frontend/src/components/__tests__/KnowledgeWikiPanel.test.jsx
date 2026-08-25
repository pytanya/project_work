import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import KnowledgeWikiPanel from '../KnowledgeWikiPanel'

describe('KnowledgeWikiPanel', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('отсекает навигационные/скрап-темы базы знаний, сохраняя реальные', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        subjects: [
          {
            subject: 'Литература',
            articles: [
              { topic: 'Поэты серебряного века', title: 'Поэты серебряного века', mastery: 0.5 },
              { topic: 'Картинки', title: 'Картинки', mastery: 0.5 },
              { topic: 'Параграф 24', title: 'Параграф 24', mastery: 0.5 },
              { topic: 'По теме: методические разработки, презентации и конспекты', title: 'По теме: методические разработки, презентации и конспекты', mastery: 0.5 },
              { topic: 'Главным своим результатом считаю…', title: 'Главным своим результатом считаю…', mastery: 0.5 },
            ],
          },
        ],
      }),
    })
    render(<KnowledgeWikiPanel studentId="stu_junk" />)
    await waitFor(() => expect(screen.getByText(/База знаний/)).toBeInTheDocument())
    // мусор отфильтрован, реальная тема осталась
    expect(screen.queryByText(/Картинки/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Параграф 24/)).not.toBeInTheDocument()
    expect(screen.queryByText(/методические разработки/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Главным своим результатом/)).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /Поэты серебряного века/ }).length).toBeGreaterThan(0)
  })

  it('показывает «Знания накапливаются» без профиля ученика и не делает запрос', async () => {
    global.fetch = vi.fn()
    render(<KnowledgeWikiPanel />)
    expect(screen.getByText(/Знания накапливаются/)).toBeInTheDocument()
    // запроса к API быть не должно — нет student_id
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('показывает «Знания накапливается» при пустой базе', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ subjects: [] }) })
    render(<KnowledgeWikiPanel studentId="stu_empty" />)
    await waitFor(() => expect(screen.getByText(/Знания накапливаются/)).toBeInTheDocument())
  })

  it('показывает темы с мастерством по предметам', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        subjects: [
          {
            subject: 'Философия',
            articles: [
              { topic: 'Кант', title: 'Кант', mastery: 0.85, correct: 4, attempts: 5 },
              { topic: 'Гегель', title: 'Гегель', mastery: 0.3, correct: 1, attempts: 4 },
            ],
          },
        ],
      }),
    })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_test" />)
    await waitFor(() => expect(screen.getByText(/База знаний · 2/)).toBeInTheDocument())
    // статистика по темам
    expect(screen.getByText('тем')).toBeInTheDocument()
    // секция предмета и статьи с процентом мастерства (браузер, не heat-map усвоения)
    expect(screen.getByRole('button', { name: /Философия/ })).toBeInTheDocument()
    expect(container.querySelector('.wiki-subject__count').textContent).toBe('2')
    const browser = container.querySelector('.wiki-subjects')
    expect(within(browser).getByRole('button', { name: /Кант/ })).toBeInTheDocument()
    expect(within(browser).getByRole('button', { name: /Гегель/ })).toBeInTheDocument()
    expect(screen.getByText('85%')).toBeInTheDocument()
    expect(screen.getByText('30%')).toBeInTheDocument()
  })

  it('не падает при ошибке сети', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('network'))
    render(<KnowledgeWikiPanel studentId="stu_test" />)
    await waitFor(() => expect(document.querySelector('.card.wiki-panel')).not.toBeNull())
    expect(await screen.findByText(/Не удалось загрузить: network/)).toBeInTheDocument()
  })

  it('открывает тему в модальном окне для чтения по клику', async () => {
    const user = userEvent.setup()
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        subjects: [
          {
            subject: 'Философия',
            articles: [
              { topic: 'Кант', title: 'Кант', mastery: 0.85, correct: 4, attempts: 5,
                source: 'https://example.com/kant', notes: ['ошибка в императиве'],
                concepts: ['императив', 'априори'], body: 'Кант — основоположник критической философии.' },
            ],
          },
        ],
      }),
    })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_test" />)
    await screen.findByRole('button', { name: /Философия/ })  // дождались данных
    const browser = container.querySelector('.wiki-subjects')
    await user.click(within(browser).getByRole('button', { name: /Кант/ }))
    // модальное чтение: заголовок, источник, изложение, понятия, заметки
    const modal = await screen.findByRole('dialog')
    expect(modal).toBeInTheDocument()
    expect(within(modal).getByText('Кант')).toBeInTheDocument()
    expect(within(modal).getByText(/https:\/\/example.com\/kant/)).toBeInTheDocument()
    expect(within(modal).getByText('Кант — основоположник критической философии.')).toBeInTheDocument()
    expect(within(modal).getByText('императив')).toBeInTheDocument()
    expect(within(modal).getByText('ошибка в императиве')).toBeInTheDocument()
  })

  it('закрывает модал по клику на фон', async () => {
    const user = userEvent.setup()
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        subjects: [
          { subject: 'Философия', articles: [{ topic: 'Кант', title: 'Кант', mastery: 0.85, body: 'Текст' }] },
        ],
      }),
    })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_test" />)
    await screen.findByRole('button', { name: /Философия/ })
    const browser = container.querySelector('.wiki-subjects')
    await user.click(within(browser).getByRole('button', { name: /Кант/ }))
    await screen.findByRole('dialog')
    await user.click(document.querySelector('.topic-modal-backdrop'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('предлагает сгенерировать изложение, когда тело — плейсхолдер, и не показывает «unverified»', async () => {
    const user = userEvent.setup()
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        subjects: [
          { subject: 'Информатика', articles: [
            { topic: 'Системы счисления', title: 'Системы счисления', mastery: 0.46,
              attempts: 2, correct: 1, curriculum: 'unverified',
              body: 'Материал по теме «Системы счисления» накапливается по мере прохождения квизов.' },
          ] },
        ],
      }),
    })
    const { container } = render(<KnowledgeWikiPanel studentId="stu_test" />)
    await screen.findByRole('button', { name: /Информатика/ })
    const browser = container.querySelector('.wiki-subjects')
    await user.click(within(browser).getByRole('button', { name: /Системы счисления/ }))
    const modal = await screen.findByRole('dialog')
    // «unverified» и плейсхолдер скрыты; есть кнопка генерации изложения
    expect(within(modal).queryByText(/unverified/)).not.toBeInTheDocument()
    expect(within(modal).queryByText(/накапливается/)).not.toBeInTheDocument()
    expect(within(modal).getByRole('button', { name: /Сгенерировать изложение/ })).toBeInTheDocument()
  })
})
