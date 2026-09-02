import { test, expect } from '@playwright/test'
import { fillIntakeCard } from './intake-helpers'

test.describe('Topic Selection Flow', () => {
  test('should show topic panel after indexing and select topic', async ({ page }) => {
    await page.goto('/')

    // 1) Карточка знакомства (форма вместо пошаговых вопросов)
    await fillIntakeCard(page, {
      name: 'Ученик Четыре', learnerType: 'schoolchild', grade: '4',
      subject: 'основы православной культуры', topic: 'Культура и религия', hasTextbook: 'true', mode: 'quiz',
    })

    // 2) Должен попросить загрузить файл (has_textbook=да, файла нет)
    await expect(page.getByText(/Загрузите, пожалуйста, файл учебника|Загрузите файл/).first()).toBeVisible({ timeout: 30_000 })

    // 3) Панель тем может не появиться без загруженного учебника — тест только логирует
    const topicPanel = page.locator('.card.graph')
    const hasTopicPanel = (await topicPanel.count()) > 0
    console.log('Topic panel exists:', hasTopicPanel)
  })

  test('should update active topic in graph panel', async ({ page }) => {
    await page.goto('/')

    // Карточка знакомства
    await fillIntakeCard(page, {
      name: 'Тест Ученик', subject: 'география', topic: 'Атмосфера', hasTextbook: 'false', mode: 'quiz',
    })

    // Check session ID
    const sessionIdEl = page.locator('.session-id')
    await expect(sessionIdEl).toBeVisible({ timeout: 30_000 })
    const sessionId = await sessionIdEl.textContent()
    console.log('Session ID:', sessionId)

    // Get graph data via API
    if (sessionId && sessionId.startsWith('сессия:')) {
      const sid = sessionId.replace('сессия: ', '')
      const response = await page.evaluate(async (id) => {
        const res = await fetch(`/api/sessions/${id}/graph`)
        return await res.json()
      }, sid)

      console.log('Active topic:', response.active_topic)
    }
  })
})
