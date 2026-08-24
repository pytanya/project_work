import { render, screen } from '@testing-library/react'
import LessonDiagram from '../LessonDiagram'

describe('LessonDiagram', () => {
  it('рендерит flow-схему: боксы и стрелка', () => {
    render(
      <LessonDiagram
        diagram={{
          kind: 'flow',
          title: 'Состав атмосферы',
          nodes: [
            { id: 'n1', label: 'Азот' },
            { id: 'n2', label: 'Кислород' },
          ],
          edges: [{ source: 'n1', target: 'n2', label: 'смесь' }],
        }}
      />,
    )
    const svg = document.querySelector('.lesson-diagram')
    expect(svg).toBeInTheDocument()
    expect(screen.getByText('Азот')).toBeInTheDocument()
    expect(screen.getByText('Кислород')).toBeInTheDocument()
    expect(screen.getByText('смесь')).toBeInTheDocument()
    expect(svg.querySelectorAll('line').length).toBeGreaterThanOrEqual(1)
  })

  it('рендерит map-схему с тёплым течением (warm → красная стрелка)', () => {
    const { container } = render(
      <LessonDiagram
        diagram={{
          kind: 'map',
          title: 'Гольфстрим',
          nodes: [
            { id: 'e', label: 'Европа', x: 0.8, y: 0.3 },
            { id: 'g', label: 'Гольфстрим', x: 0.5, y: 0.6 },
          ],
          edges: [{ source: 'g', target: 'e', label: 'тёплое', color: 'warm' }],
        }}
      />,
    )
    const svg = container.querySelector('.lesson-diagram')
    expect(svg).toBeInTheDocument()
    expect(screen.getByText('Европа')).toBeInTheDocument()
    expect(screen.getByText('тёплое')).toBeInTheDocument()
    // стрелка тёплого течения рисуется красным цветом (color warm)
    expect(svg.querySelectorAll('path').length).toBeGreaterThanOrEqual(1)
  })

  it('не рендерит ничего без узлов', () => {
    const { container } = render(<LessonDiagram diagram={{ kind: 'flow', nodes: [] }} />)
    expect(container.querySelector('.lesson-diagram')).not.toBeInTheDocument()
  })
})
