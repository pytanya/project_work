import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import KnowledgeGraphPanel from '../KnowledgeGraphPanel'

const nodes = [
  { id: 'book:x', title: 'Учебник «x»', type: 'book', color: '#F4A261' },
  { id: 'sec:x:1', title: 'Урок 1: Россия — наша Родина', type: 'section', color: '#64DFDF' },
  { id: 'sec:x:2', title: 'Урок 2: Культура и религия', type: 'section', color: '#B388FF' },
]

describe('KnowledgeGraphPanel', () => {
  it('не рендерится без узлов', () => {
    const { container } = render(<KnowledgeGraphPanel nodes={[]} onSelect={() => {}} />)
    expect(container.querySelector('.card.graph')).not.toBeInTheDocument()
  })

  it('показывает темы (без корневого учебника)', () => {
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    expect(screen.getByRole('button', { name: 'Урок 1: Россия — наша Родина' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Урок 2: Культура и религия' })).toBeInTheDocument()
    expect(screen.getByRole('img', { name: /Граф тем учебника/ })).toBeInTheDocument()
  })

  it('вызывает onSelect по клику', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={onSelect} />)
    await user.click(screen.getByRole('button', { name: 'Урок 2: Культура и религия' }))
    expect(onSelect).toHaveBeenCalledWith(nodes[2])
  })

  it('показывает активную тему', () => {
    render(<KnowledgeGraphPanel nodes={nodes} activeTopic="sec:x:1" onSelect={() => {}} />)
    expect(screen.getByText(/Изучаем:/)).toBeInTheDocument()
  })

  it('фильтрует темы по поиску', async () => {
    const user = userEvent.setup()
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    await user.type(screen.getByPlaceholderText('Найти тему…'), 'Культура')
    expect(screen.getByRole('button', { name: 'Урок 2: Культура и религия' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Урок 1: Россия — наша Родина' })).not.toBeInTheDocument()
  })

  it('показывает счётчик тем', () => {
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    expect(screen.getByText(/Темы учебника · 2/)).toBeInTheDocument()
  })

  it('отображает mastery-метку в подсказке узла (roadmap #3)', () => {
    const withMastery = [
      ...nodes,
      { id: 'sec:x:3', title: 'Урок 3: Традиции', type: 'section', color: '#69F0AE', mastery: 0.85, attempts: 5 },
    ]
    render(<KnowledgeGraphPanel nodes={withMastery} onSelect={() => {}} />)
    const chip = screen.getByRole('button', { name: /Урок 3: Традиции/ })
    expect(chip.getAttribute('title')).toContain('мастерство 85%')
  })

  it('показывает легенду рёбер и mastery-цветов', () => {
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    expect(screen.getByText(/опирается на/)).toBeInTheDocument()
    expect(screen.getByText(/входит в/)).toBeInTheDocument()
    expect(screen.getByText(/связан/)).toBeInTheDocument()
    expect(screen.getByText(/высокое/)).toBeInTheDocument()
  })

  it('drill-down: клик по узлу открывает панель wiki (roadmap #3)', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        node: nodes[2],
        wiki: { title: 'Урок 2', topic: 'Культура', mastery: 0.6, attempts: 3, accuracy: 0.33, notes: ['ошибка'], body: 'Тело статьи' },
      }),
    })
    const user = userEvent.setup()
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    await user.click(screen.getByRole('button', { name: 'Урок 2: Культура и религия' }))
    const panel = await screen.findByText(/Мастерство: 60%/)
    expect(panel).toBeInTheDocument()
    expect(screen.getByText(/Тело статьи/)).toBeInTheDocument()
  })

  it('показывает тултип с данными при наведении на узел (Obsidian-стиль)', async () => {
    const user = userEvent.setup()
    const { container } = render(
      <KnowledgeGraphPanel
        nodes={nodes}
        edges={[{ source: 'book:x', target: 'sec:x:1', relation: 'part_of' }]}
        onSelect={() => {}}
      />,
    )
    const topicNode = container.querySelector('.kg-node:not(.kg-book)')
    await user.hover(topicNode)
    const tt = await (() => {
      const el = container.querySelector('.kg-tooltip')
      return Promise.resolve(el)
    })()
    expect(tt).toBeTruthy()
    expect(tt.textContent).toContain('Урок 1: Россия — наша Родина')
    expect(tt.textContent).toContain('Раздел')
    expect(tt.textContent).toContain('связей: 1')
  })
})
