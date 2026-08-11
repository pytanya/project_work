import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import QuizCard from '../QuizCard'

const question = {
  question: 'Что такое атмосфера?',
  options: ['А', 'Б', 'В'],
  answerType: 'single',
  difficulty: 'easy',
  topic: 'Атмосфера',
}

describe('QuizCard', () => {
  it('отображает вопрос, варианты и бейджи', () => {
    render(<QuizCard q={question} onSelect={() => {}} />)
    expect(screen.getByText('Что такое атмосфера?')).toBeInTheDocument()
    expect(screen.getByText('А')).toBeInTheDocument()
    expect(screen.getByText('easy')).toBeInTheDocument()
    expect(screen.getByText('Атмосфера')).toBeInTheDocument()
  })

  it('вызывает onSelect при клике на вариант', async () => {
    const user = userEvent.setup()
    const onSelect = vi.fn()
    render(<QuizCard q={question} onSelect={onSelect} />)
    await user.click(screen.getByText('Б'))
    expect(onSelect).toHaveBeenCalledWith('Б')
  })

  it('не рисует варианты для открытого вопроса', () => {
    const { container } = render(
      <QuizCard q={{ ...question, options: null, answerType: 'open' }} onSelect={() => {}} />,
    )
    expect(container.querySelector('.options')).not.toBeInTheDocument()
  })
})
