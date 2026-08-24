import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MasteryWall from '../MasteryWall'

const subjects = [
  { subject: 'Философия', articles: [
    { topic: 'Кант', mastery: 0.85 },
    { topic: 'Гегель', mastery: 0.3 },
  ]},
  { subject: 'Литература', articles: [
    { topic: 'Поэты', mastery: 0.5 },
  ]},
]

describe('MasteryWall', () => {
  it('показывает heat-map тем по предметам с цветом мастерства', () => {
    render(<MasteryWall subjects={subjects} onSelect={() => {}} />)
    expect(screen.getByText(/Усвоение/)).toBeInTheDocument()
    expect(screen.getByText('1/3 · 33%')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Кант' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Гегель' })).toBeInTheDocument()
    expect(screen.getByText('Философия')).toBeInTheDocument()
    expect(screen.getByText('Литература')).toBeInTheDocument()
  })

  it('сворачивается и разворачивается', async () => {
    const user = userEvent.setup()
    const { container } = render(<MasteryWall subjects={subjects} onSelect={() => {}} />)
    await user.click(screen.getByRole('button', { name: /Усвоение/ }))
    expect(container.querySelector('.mastery-wall__grid')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Усвоение/ }))
    expect(container.querySelector('.mastery-wall__grid')).toBeInTheDocument()
  })

  it('вызывает onSelect по клику на ячейку', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<MasteryWall subjects={subjects} onSelect={onSelect} />)
    await user.click(screen.getByRole('button', { name: 'Кант' }))
    expect(onSelect).toHaveBeenCalledWith('Философия', 'Кант')
  })

  it('не рендерится без тем', () => {
    const { container } = render(<MasteryWall subjects={[]} onSelect={() => {}} />)
    expect(container.querySelector('.mastery-wall')).not.toBeInTheDocument()
  })
})
