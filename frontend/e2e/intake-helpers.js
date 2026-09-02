import { expect } from '@playwright/test'

// Заполнение карточки знакомства (детерминированные поля build_intake_card)
// и ожидание перехода к следующему шагу (загрузка файла / план занятия).
export async function fillIntakeCard(page, {
  name = 'Тест Ученик',
  learnerType = 'student',
  grade = '',
  subject,
  topic,
  hasTextbook = 'false',
  mode = 'quiz',
}) {
  await expect(page.getByText('Заполни карточку').first()).toBeVisible({ timeout: 30_000 })
  await page.getByLabel(/Как тебя зовут/).fill(name)
  await page.getByLabel(/Ты школьник или студент/).selectOption(learnerType)
  if (grade) await page.getByLabel(/Класс/).fill(grade)
  await page.getByLabel(/Предмет/).fill(subject)
  await page.getByLabel(/Тема/).fill(topic)
  await page.getByLabel(/Есть учебник по теме/).selectOption(hasTextbook)
  await page.getByLabel(/Что делаем/).selectOption(mode)
  await page.getByRole('button', { name: 'Начать занятие' }).click()
}
