import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { fireEvent } from '@testing-library/dom'
import KnowledgeGraphPanel from '../KnowledgeGraphPanel'

const nodes = [
  { id: 'book:x', title: 'Учебник «x»', type: 'book', color: '#F4A261' },
  { id: 'sec:x:1', title: 'Урок 1: Россия — наша Родина', type: 'topic', color: '#64DFDF' },
  { id: 'sec:x:2', title: 'Урок 2: Культура и религия', type: 'topic', color: '#B388FF' },
]

function makeMockCtx() {
  const gradient = { addColorStop: () => {} }
  return new Proxy({}, {
    get(target, prop) {
      if (prop === 'createLinearGradient' || prop === 'createRadialGradient') return () => gradient
      if (prop === 'measureText') return () => ({ width: 0 })
      return () => {}
    },
    set() { return true },
  })
}

const RECT = { left: 0, top: 0, width: 600, height: 260, right: 600, bottom: 260, x: 0, y: 0, toJSON: () => {} }

beforeEach(() => {
  // jsdom не реализует canvas 2D-контекст — заглушка, чтобы анимационный цикл не падал
  HTMLCanvasElement.prototype.getContext = () => makeMockCtx()
  // симуляция раскладывается по размерам канваса
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 600 })
})

afterEach(() => {
  vi.restoreAllMocks()
  delete HTMLElement.prototype.offsetWidth
})

describe('KnowledgeGraphPanel', () => {
  it('без узлов показывает дружелюбное пустое состояние', () => {
    render(<KnowledgeGraphPanel nodes={[]} onSelect={() => {}} />)
    expect(screen.getByText(/Загрузите учебник или найдите источник/)).toBeInTheDocument()
  })

  it('показывает темы (без корневого учебника) и канвас-граф', () => {
    const { container } = render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    expect(screen.getByRole('button', { name: 'Урок 1: Россия — наша Родина' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Урок 2: Культура и религия' })).toBeInTheDocument()
    expect(container.querySelector('canvas.graph-canvas')).toBeInTheDocument()
  })

  it('клик по узлу открывает карточку; изучение — по кнопке «Изучить тему»', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ node: nodes[2], wiki: null, related: [] }),
    })
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={onSelect} />)
    // клик по чипу НЕ запускает изучение (осознанный шаг), а открывает карточку
    await user.click(screen.getByRole('button', { name: 'Урок 2: Культура и религия' }))
    expect(onSelect).not.toHaveBeenCalled()
    // в карточке — кнопка «Изучить тему»
    const study = await screen.findByRole('button', { name: /Изучить тему/ })
    await user.click(study)
    expect(onSelect).toHaveBeenCalledWith(nodes[2])
  })

  it('показывает активную тему', () => {
    render(<KnowledgeGraphPanel nodes={nodes} activeTopic="sec:x:1" onSelect={() => {}} />)
    expect(screen.getByText(/Изучаем:/)).toBeInTheDocument()
  })

  it('фильтрует темы по поиску', async () => {
    const user = userEvent.setup()
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    await user.type(screen.getByPlaceholderText(/Найти тему/), 'Культура')
    expect(screen.getByRole('button', { name: 'Урок 2: Культура и религия' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Урок 1: Россия — наша Родина' })).not.toBeInTheDocument()
  })

  it('показывает счётчик тем', () => {
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    expect(screen.getByText(/Граф знаний · 2/)).toBeInTheDocument()
  })

  it('скрывает структурные узлы (разделы/уроки) по умолчанию и показывает по тумблеру', async () => {
    const withStructural = [
      ...nodes,
      { id: 'sec:y:1', title: 'Параграф 5: Символизм', type: 'section', color: '#B388FF' },
      { id: 'sec:y:2', title: 'Урок 4: Акмеизм', type: 'lesson', color: '#64DFDF' },
    ]
    const user = userEvent.setup()
    const { rerender } = render(<KnowledgeGraphPanel nodes={withStructural} onSelect={() => {}} />)
    // по умолчанию структурные узлы скрыты
    expect(screen.queryByRole('button', { name: 'Параграф 5: Символизм' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Урок 4: Акмеизм' })).not.toBeInTheDocument()
    // тумблер 🧩 показывает их
    await user.click(screen.getByRole('button', { name: '🧩' }))
    rerender(<KnowledgeGraphPanel nodes={withStructural} onSelect={() => {}} />)
    expect(screen.getByRole('button', { name: 'Параграф 5: Символизм' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Урок 4: Акмеизм' })).toBeInTheDocument()
  })

  it('отображает mastery-метку в подсказке узла (roadmap #3)', () => {
    const withMastery = [
      ...nodes,
      { id: 'sec:x:3', title: 'Урок 3: Традиции', type: 'topic', color: '#69F0AE', mastery: 0.85, attempts: 5 },
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
        wiki: { title: 'Урок 2', topic: 'Культура', mastery: 0.6, attempts: 3, accuracy: 0.33, notes: ['ошибка'], concepts: ['артефакт', 'ритуал'], body: 'Тело статьи' },
      }),
    })
    const user = userEvent.setup()
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    await user.click(screen.getByRole('button', { name: 'Урок 2: Культура и религия' }))
    const panel = await screen.findByText(/60% · попыток: 3/)
    expect(panel).toBeInTheDocument()
    expect(screen.getByText(/Тело статьи/)).toBeInTheDocument()
    // Ключевые понятия темы (roadmap #3)
    expect(screen.getByText(/Ключевые понятия/)).toBeInTheDocument()
    expect(screen.getByText('артефакт')).toBeInTheDocument()
    expect(screen.getByText('ритуал')).toBeInTheDocument()
  })

  it('группирует подтемы под родителем (не книгой)', () => {
    const nodesWithParent = [
      ...nodes,
      { id: 'page:web:0', title: 'ru.wikipedia.org', type: 'topic', color: '#69F0AE', parent_id: 'book:x' },
      { id: 'sec:web:0', title: 'Жизнь и биография', type: 'topic', color: '#69F0AE', parent_id: 'page:web:0' },
      { id: 'sec:web:1', title: 'Критика чистого разума', type: 'topic', color: '#69F0AE', parent_id: 'page:web:0' },
    ]
    render(<KnowledgeGraphPanel nodes={nodesWithParent} onSelect={() => {}} />)
    // родитель — заголовок группы (кнопка), подтемы — чипы внутри группы; родитель не дублируется чипом
    expect(screen.getByRole('button', { name: /ru.wikipedia.org/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Жизнь и биография' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Критика чистого разума' })).toBeInTheDocument()
  })

  it('drill-down показывает связанные темы (roadmap #3)', async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        node: nodes[2],
        related: [
          { source: 'sec:x:2', target: 'sec:x:1', relation: 'related',
            target_title: 'Урок 1: Россия — наша Родина', target_type: 'section' },
        ],
      }),
    })
    const user = userEvent.setup()
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    await user.click(screen.getByRole('button', { name: 'Урок 2: Культура и религия' }))
    const relatedBox = await screen.findByText(/Связанные темы/)
    expect(relatedBox).toBeInTheDocument()
    expect(within(relatedBox.closest('.graph-wiki-related')).getByRole('button', { name: 'Урок 1: Россия — наша Родина' })).toBeInTheDocument()
  })

  it('открывает граф в плавающем окне (портал) и закрывает по фону', async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ node: null, wiki: null, related: [] }) })
    const user = userEvent.setup()
    render(<KnowledgeGraphPanel nodes={nodes} onSelect={() => {}} />)
    // открыть в окне (кнопка ⛶)
    await user.click(screen.getByRole('button', { name: '⛶' }))
    const backdrop = document.querySelector('.graph-float-backdrop')
    expect(backdrop).toBeInTheDocument()  // портал в document.body
    expect(backdrop.querySelector('.graph-panel--float')).toBeInTheDocument()
    // закрыть по клику на фон
    await user.click(backdrop)
    expect(document.querySelector('.graph-float-backdrop')).not.toBeInTheDocument()
  })

  it('показывает тултип с данными при наведении на узел (Obsidian-стиль)', () => {
    const { container } = render(
      <KnowledgeGraphPanel
        nodes={nodes}
        edges={[{ source: 'book:x', target: 'sec:x:1', relation: 'part_of' }]}
        onSelect={() => {}}
      />,
    )
    const wrap = container.querySelector('.graph-canvas-wrap')
    const canvas = container.querySelector('canvas.graph-canvas')
    wrap.getBoundingClientRect = () => RECT
    canvas.getBoundingClientRect = () => RECT
    // mousemove в центр канваса — там закреплён корневой узел учебника
    fireEvent.mouseMove(wrap, { clientX: 300, clientY: 130 })
    const tt = container.querySelector('.kg-tooltip')
    expect(tt).toBeTruthy()
    expect(tt.textContent).toContain('Учебник «x»')
    expect(tt.textContent).toContain('Учебник')
    expect(tt.textContent).toContain('связей: 1')
  })
})
