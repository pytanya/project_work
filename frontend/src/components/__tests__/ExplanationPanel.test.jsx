import { render, screen } from '@testing-library/react'
import ExplanationPanel from '../ExplanationPanel'

describe('ExplanationPanel', () => {
  it('отображает текст объяснения и цитату', () => {
    render(
      <ExplanationPanel
        text="Атмосфера — газовая оболочка."
        citation={{ paragraph: '§12', source: 'Алексеев' }}
      />,
    )
    expect(screen.getByText(/газовая оболочка/)).toBeInTheDocument()
    expect(screen.getByText(/§12/)).toBeInTheDocument()
  })

  it('не показывает цитату, если её нет', () => {
    render(<ExplanationPanel text="текст" citation={{}} />)
    expect(screen.queryByText(/Источник:/)).not.toBeInTheDocument()
  })
})
