import { render, screen, waitFor } from '@testing-library/react'
import KnowledgeWikiPanel from '../KnowledgeWikiPanel'

describe('KnowledgeWikiPanel', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('показывает «накапливаются» при пустой базе', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ subjects: [] }) })
    render(<KnowledgeWikiPanel />)
    await waitFor(() => expect(screen.getByText(/накапливаются между сессиями/)).toBeInTheDocument())
  })

  it('показывает темы с мастерством по предметам (donut-chart)', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        subjects: [
          {
            subject: 'Философия',
            articles: [
              { topic: 'Кант', title: 'Кант', mastery: 0.85, accuracy: 0.8, attempts: 5 },
              { topic: 'Гегель', title: 'Гегель', mastery: 0.3, accuracy: 0.25, attempts: 4 },
            ],
          },
        ],
      }),
    })
    render(<KnowledgeWikiPanel />)
    // donut-диаграмма тем с числом изученных
    await waitFor(() =>
      expect(screen.getByRole('img', { name: /Круговая диаграмма тем базы знаний/ })).toBeInTheDocument(),
    )
    expect(screen.getByText('тем изучено')).toBeInTheDocument()
    // легенда предметов в SVG
    expect(screen.getByText(/Философия \(2\)/)).toBeInTheDocument()
  })

  it('не падает при ошибке сети', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('network'))
    render(<KnowledgeWikiPanel />)
    await waitFor(() => expect(document.querySelector('.card.wiki')).not.toBeNull())
  })

  it('показывает детали темы при наведении на сектор (tooltip)', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        subjects: [
          {
            subject: 'Философия',
            articles: [
              { topic: 'Кант', title: 'Кант', mastery: 0.85, accuracy: 0.8, attempts: 5, correct: 4, notes: ['ошибка в императиве'] },
            ],
          },
        ],
      }),
    })
    render(<KnowledgeWikiPanel />)
    const chart = await screen.findByRole('img', { name: /Круговая диаграмма/ })
    expect(chart).toBeInTheDocument()
    // tooltip-контент доступен через SVG <title> (браузерный)
    const sliceTitle = chart.querySelector('title')
    expect(sliceTitle).toBeTruthy()
    expect(sliceTitle.textContent).toContain('Кант')
    expect(sliceTitle.textContent).toContain('Предмет: Философия')
    expect(sliceTitle.textContent).toContain('Мастерство: 85%')
  })
})
