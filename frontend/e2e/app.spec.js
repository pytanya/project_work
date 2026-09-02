import { test, expect } from '@playwright/test'

// E2E: реальный стек React (Vite) → proxy → FastAPI → граф.
// Проходим intake-фазу через карточку знакомства (детерминированно, без LLM),
// чтобы тест был быстрым; вопросы квиза требуют живых LLM/поиска — отдельный сценарий.

async function fillIntakeCard(page) {
  // Карточка знакомства: поля формируются детерминированно (build_intake_card)
  await expect(page.getByText('Заполни карточку').first()).toBeVisible({ timeout: 30_000 })
  await page.getByLabel(/Как тебя зовут/).fill('Тест Ученик')
  await page.getByLabel(/Ты школьник или студент/).selectOption('student')
  await page.getByLabel(/Предмет/).fill('география')
  await page.getByLabel(/Тема/).fill('Атмосфера')
  await page.getByLabel(/Есть учебник по теме/).selectOption('false')
  await page.getByLabel(/Что делаем/).selectOption('quiz')
  await page.getByRole('button', { name: 'Начать занятие' }).click()
}

test('сессия создаётся и приходит карточка знакомства', async ({ page }) => {
  await page.goto('/')

  // карточка знакомства показывается (форма вместо пошаговых вопросов)
  await expect(page.getByText('Заполни карточку').first()).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('Как тебя зовут').first()).toBeVisible()
})

test('заполнение карточки переводит к плану занятия', async ({ page }) => {
  await page.goto('/')
  await fillIntakeCard(page)

  // после отправки карточки интэйк завершён — план/режим принят в чате
  await expect(page.getByText('Карточка знакомства заполнена').first()).toBeVisible({ timeout: 30_000 })
})
