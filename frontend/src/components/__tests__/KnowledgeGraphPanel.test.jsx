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
})
