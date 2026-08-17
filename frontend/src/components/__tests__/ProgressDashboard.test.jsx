import { render, screen } from '@testing-library/react'
import ProgressDashboard from '../ProgressDashboard'

describe('ProgressDashboard', () => {
  it('показывает пустое состояние', () => {
    render(<ProgressDashboard knowledge={{}} />)
    expect(screen.getByText(/Пока нет данных/)).toBeInTheDocument()
  })

  it('рисует карту знаний с процентами', () => {
    render(<ProgressDashboard knowledge={{ Атмосфера: 0.65, Литосфера: 0.3 }} />)
    expect(screen.getByText('Атмосфера')).toBeInTheDocument()
    expect(screen.getByText('65%')).toBeInTheDocument()
    expect(screen.getByText('Литосфера')).toBeInTheDocument()
    expect(screen.getByText('30%')).toBeInTheDocument()
  })

  it('показывает счёт правильных ответов', () => {
    render(<ProgressDashboard knowledge={{}} correct={3} total={5} />)
    // «3/5» — в отдельном <span> (для CSS-анимации bump), поэтому матчим по частям
    expect(screen.getByText('Правильных:')).toBeInTheDocument()
    expect(screen.getByText('3/5')).toBeInTheDocument()
    expect(screen.getByText('(60%)')).toBeInTheDocument()
  })
})
