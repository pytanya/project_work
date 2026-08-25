import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SourceWhitelistPanel from '../SourceWhitelistPanel'

function mockPolicy(body) {
  global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => body })
}

describe('SourceWhitelistPanel', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('показывает «любые источники» по умолчанию и раскрывает список', async () => {
    mockPolicy({ allow_any_sources: true, whitelist: [] })
    render(<SourceWhitelistPanel studentId="stu_1" />)
    await waitFor(() => expect(screen.getByText(/любые/)).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: /Источники/ }))
    // чекбокс «Любые источники» включён
    expect(screen.getByLabelText(/Любые источники/)).toBeChecked()
  })

  it('сохраняет белый список через PUT', async () => {
    const puts = []
    global.fetch = vi.fn((url, opts) => {
      if (opts?.method === 'PUT') {
        puts.push(JSON.parse(opts.body))
        return Promise.resolve({ ok: true, json: async () => ({ allow_any_sources: false, whitelist: ['wikibooks.org', 'lc.rt.ru'] }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({ allow_any_sources: false, whitelist: ['wikibooks.org'] }) })
    })
    render(<SourceWhitelistPanel studentId="stu_2" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Источники/ })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: /Источники/ }))

    const textarea = screen.getByLabelText(/Белый список доменов/)
    await userEvent.clear(textarea)
    await userEvent.type(textarea, 'wikibooks.org, https://lc.rt.ru/classbook')
    await userEvent.click(screen.getByRole('button', { name: /Сохранить/ }))

    await waitFor(() => expect(puts.length).toBe(1))
    expect(puts[0].allow_any_sources).toBe(false)
    expect(puts[0].whitelist).toEqual(['wikibooks.org', 'https://lc.rt.ru/classbook'])
    await waitFor(() => expect(screen.getByText(/Политика источников сохранена/)).toBeInTheDocument())
  })

  it('отклоняет сохранение пустого списка при выключенных «любых источниках»', async () => {
    const puts = []
    global.fetch = vi.fn((url, opts) => {
      if (opts?.method === 'PUT') {
        puts.push(JSON.parse(opts.body))
        return Promise.resolve({ ok: true, json: async () => ({ allow_any_sources: false, whitelist: [] }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({ allow_any_sources: false, whitelist: ['wikibooks.org'] }) })
    })
    render(<SourceWhitelistPanel studentId="stu_3" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Источники/ })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: /Источники/ }))
    const textarea = screen.getByLabelText(/Белый список доменов/)
    await userEvent.clear(textarea)
    await userEvent.click(screen.getByRole('button', { name: /Сохранить/ }))
    // пустой список → сохранение отклонено, PUT не отправлен
    expect(puts.length).toBe(0)
    expect(screen.getByText(/добавьте хотя бы один домен/i)).toBeInTheDocument()
  })

  it('выключение «любых» с пустым списком открывает редактор и сохраняет политику', async () => {
    const puts = []
    global.fetch = vi.fn((url, opts) => {
      if (opts?.method === 'PUT') {
        puts.push(JSON.parse(opts.body))
        return Promise.resolve({ ok: true, json: async () => ({ allow_any_sources: false, whitelist: [] }) })
      }
      return Promise.resolve({ ok: true, json: async () => ({ allow_any_sources: true, whitelist: [] }) })
    })
    render(<SourceWhitelistPanel studentId="stu_5" />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Источники/ })).toBeInTheDocument())
    await userEvent.click(screen.getByRole('button', { name: /Источники/ }))
    expect(screen.getByLabelText(/Любые источники/)).toBeChecked()
    await userEvent.click(screen.getByLabelText(/Любые источники/))
    await waitFor(() => expect(puts.length).toBe(1))
    expect(puts[0].allow_any_sources).toBe(false)
    // редактор белого списка открылся
    expect(screen.getByLabelText(/Белый список доменов/)).toBeInTheDocument()
  })

  it('раскрывается по внешнему сигналу openSignal', async () => {
    mockPolicy({ allow_any_sources: false, whitelist: ['wikibooks.org'] })
    const { rerender } = render(<SourceWhitelistPanel studentId="stu_4" openSignal={0} />)
    await waitFor(() => expect(screen.getByRole('button', { name: /Источники/ })).toBeInTheDocument())
    expect(screen.queryByLabelText(/Белый список доменов/)).not.toBeInTheDocument()
    rerender(<SourceWhitelistPanel studentId="stu_4" openSignal={1} />)
    await waitFor(() => expect(screen.getByLabelText(/Белый список доменов/)).toBeInTheDocument())
  })
})
