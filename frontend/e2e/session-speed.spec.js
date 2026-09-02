import { test, expect } from '@playwright/test'

test.describe('EduTutor - Session Creation Speed', () => {
  test('session should be created quickly and show first intake question', async ({ page }) => {
    const networkErrors = []
    page.on('response', (response) => {
      if (response.status() >= 500) {
        networkErrors.push({ url: response.url(), status: response.status() })
      }
    })

    const startTime = Date.now()
    await page.goto('/')

    // Ждём session-id (создание сессии)
    await expect(page.locator('.session-id')).toBeVisible({ timeout: 30000 })
    const sessionTime = Date.now() - startTime
    console.log(`Session created in: ${sessionTime}ms`)

    // Ждём первый шаг intake (карточка знакомства приходит через WS асинхронно)
    await expect(page.locator('.card.intake-card, .card.intake, .empty-chat')).toBeVisible({ timeout: 30000 })
    const questionTime = Date.now() - startTime
    console.log(`First question visible in: ${questionTime}ms`)

    // Нет 500 ошибок
    expect(networkErrors).toEqual([])
  })
})
