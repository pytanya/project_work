import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SourceSearchPanel from '../SourceSearchPanel'

describe('SourceSearchPanel', () => {
  it('показывает статус по умолчанию', () => {
    render(<SourceSearchPanel status={null} note={null} onFind={() => {}} />)
    expect(screen.getByText(/Источник не выбран/)).toBeInTheDocument()
  })

  it('показывает статус и заметку', () => {
    render(<SourceSearchPanel status="ready" note="Собрано 2 источника" onFind={() => {}} />)
    expect(screen.getByText(/Источник готов/)).toBeInTheDocument()
    expect(screen.getByText('Собрано 2 источника')).toBeInTheDocument()
  })

  it('вызывает onFind по кнопке', async () => {
    const user = userEvent.setup()
    const onFind = vi.fn()
    render(<SourceSearchPanel status={null} note={null} onFind={onFind} />)
    await user.click(screen.getByRole('button', { name: /Найти учебник/ }))
    expect(onFind).toHaveBeenCalled()
  })

  it('показывает найденные источники с ссылками', () => {
    const sources = [
      { type: 'page', url: 'https://ru.wikipedia.org/wiki/Кант', license: 'источник лицензионно допустим' },
      { type: 'page', url: 'https://ru.wikibooks.org/wiki/Философия' },
    ]
    render(<SourceSearchPanel status="ready" note="Собрано 2 источника" sources={sources} onFind={() => {}} />)
    expect(screen.getByText(/Найденные источники \(2\):/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /wikipedia/ })).toHaveAttribute('href', 'https://ru.wikipedia.org/wiki/Кант')
    expect(screen.getByRole('link', { name: /wikibooks/ })).toBeInTheDocument()
  })

  it('показывает автора и локальный PDF', () => {
    render(
      <SourceSearchPanel
        status="ready"
        author="Алексеев А.И."
        sources={[{ type: 'local_pdf', path: 'downloads/geo.pdf' }]}
        onFind={() => {}}
      />,
    )
    expect(screen.getByText('Алексеев А.И.')).toBeInTheDocument()
    expect(screen.getByText(/Локальный учебник:/)).toBeInTheDocument()
  })

  it('не падает без источников', () => {
    render(<SourceSearchPanel status="failed" note="ничего" onFind={() => {}} />)
    expect(screen.queryByText(/Найденные источники/)).not.toBeInTheDocument()
  })
})
