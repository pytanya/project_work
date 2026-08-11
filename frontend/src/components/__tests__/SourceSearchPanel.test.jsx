import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SourceSearchPanel from '../SourceSearchPanel'

describe('SourceSearchPanel', () => {
  it('показывает статус по умолчанию', () => {
    render(<SourceSearchPanel status={null} note={null} onFind={() => {}} />)
    expect(screen.getByText(/Источник не найден/)).toBeInTheDocument()
  })

  it('показывает статус и заметку', () => {
    render(<SourceSearchPanel status="ready" note="Собрано 2 источника" onFind={() => {}} />)
    expect(screen.getByText(/Статус: ready/)).toBeInTheDocument()
    expect(screen.getByText('Собрано 2 источника')).toBeInTheDocument()
  })

  it('вызывает onFind по кнопке', async () => {
    const user = userEvent.setup()
    const onFind = vi.fn()
    render(<SourceSearchPanel status={null} note={null} onFind={onFind} />)
    await user.click(screen.getByRole('button', { name: /Найти учебник/ }))
    expect(onFind).toHaveBeenCalled()
  })
})
