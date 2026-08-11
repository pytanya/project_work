import { render, screen } from '@testing-library/react'
import ChatStream from '../ChatStream'

describe('ChatStream', () => {
  it('показывает заглушку при пустой ленте', () => {
    render(<ChatStream feed={[]} />)
    expect(screen.getByText(/Сессия создаётся/)).toBeInTheDocument()
  })

  it('рендерит разные типы сообщений', () => {
    const feed = [
      { id: 'a1', kind: 'user', text: 'привет' },
      { id: 'a2', kind: 'intake', text: 'Кто ты?' },
      { id: 'a3', kind: 'quiz', text: 'Что такое атмосфера?' },
      { id: 'a4', kind: 'explanation', text: 'разбор', data: { message: 'разбор ошибки' } },
      { id: 'a5', kind: 'summary', text: 'Квиз завершён' },
      { id: 'a6', kind: 'system', text: 'заметка' },
      { id: 'a7', kind: 'source', text: 'поиск…' },
      { id: 'a8', kind: 'error', text: 'ошибка' },
    ]
    render(<ChatStream feed={feed} />)
    expect(screen.getByText('привет')).toBeInTheDocument()
    // emoji-префиксы (📋/🎯/ℹ️/🔎/⚠️) — матчим по подстроке
    expect(screen.getByText(/Кто ты\?/)).toBeInTheDocument()
    expect(screen.getByText(/Что такое атмосфера\?/)).toBeInTheDocument()
    expect(screen.getByText('разбор ошибки')).toBeInTheDocument()
    expect(screen.getByText(/Квиз завершён/)).toBeInTheDocument()
    expect(screen.getByText(/заметка/)).toBeInTheDocument()
    expect(screen.getByText(/поиск…/)).toBeInTheDocument()
    expect(screen.getByText(/ошибка/)).toBeInTheDocument()
  })
})
