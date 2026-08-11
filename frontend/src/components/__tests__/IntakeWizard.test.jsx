import { render, screen } from '@testing-library/react'
import IntakeWizard from '../IntakeWizard'

describe('IntakeWizard', () => {
  it('отображает вопрос чек-листа', () => {
    render(<IntakeWizard missing={['learner_type']} question="Кто ты?" />)
    expect(screen.getByText('Кто ты?')).toBeInTheDocument()
  })

  it('отмечает пройденные и недостающие поля', () => {
    render(
      <IntakeWizard
        missing={['grade', 'mode']}
        question="Какой у тебя класс?"
      />,
    )
    // пройденные поля: pending — это те, что в missing
    const items = screen.getAllByText(/Предмет|Тема|Учебник|Глава/)
    expect(items.length).toBeGreaterThan(0)
    // недостающие отмечены классом pending
    expect(document.querySelectorAll('.check-item.pending').length).toBe(2)
  })
})
