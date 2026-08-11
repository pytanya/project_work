import { test, expect } from '@playwright/test'

// E2E: реальный стек React (Vite) → proxy → FastAPI → граф.
// Проходим intake-фазу (без сети/LLM — только чек-лист), чтобы тест был быстрым и
// детерминированным; вопросы квиза требуют живых LLM/поиска — отдельный сценарий.

test('сессия создаётся и приходит первый вопрос чек-листа', async ({ page }) => {
  await page.goto('/')

  // вопрос «Кто ты?» показывается в ленте и в карточке IntakeWizard — берём первый
  const question = page.getByText('Кто ты?').first()
  await expect(question).toBeVisible({ timeout: 30_000 })
  expect(await page.getByPlaceholder('Ваш ответ…').isEnabled()).toBe(true)
})

test('ответ в чек-лист переводит к следующему вопросу', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText('Кто ты?').first()).toBeVisible({ timeout: 30_000 })

  await page.getByPlaceholder('Ваш ответ…').fill('студент')
  await page.getByRole('button', { name: 'Отправить' }).click()

  await expect(page.getByText('Какой предмет изучаем?').first()).toBeVisible({ timeout: 30_000 })
})
