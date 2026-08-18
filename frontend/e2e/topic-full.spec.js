import { test, expect } from '@playwright/test'

const OPK_PDF = 'C:\\otus\\project_work\\data\\uploads\\fcde261d.pdf'

test.describe('Full Flow - Topic Selection (500 error fix)', () => {
  test('intake → upload → select topic → quiz card', async ({ page }) => {
    test.setTimeout(600_000)

    const networkErrors = []
    page.on('response', (response) => {
      if (response.status() >= 500) {
        networkErrors.push({ url: response.url(), status: response.status() })
        console.log('!!! HTTP 500:', response.url())
      }
    })

    await page.goto('/')

    // 1) Первый вопрос чек-листа
    await expect(page.getByText('Кто ты?').first()).toBeVisible({ timeout: 30000 })

    // 2) Intake
    const answers = ['ученик 4 класса', '4', 'основы православной культуры', 'Культура и религия', 'да', 'квиз']
    for (const a of answers) {
      await page.getByPlaceholder('Ваш ответ…').fill(a)
      await page.getByRole('button', { name: 'Отправить' }).click()
      await expect(page.locator('.bubble.user', { hasText: a }).last()).toBeVisible({ timeout: 15000 })
    }

    // 3) Агент просит загрузить файл
    await expect(page.getByText(/Загрузите, пожалуйста, файл учебника/).first()).toBeVisible({ timeout: 30000 })

    // 4) Загружаем учебник
    await page.setInputFiles('input[type="file"]', OPK_PDF)

    // 5) Ждём индексацию
    await expect(page.getByText(/проиндексирован/).first()).toBeVisible({ timeout: 600_000 })

    // 6) Ждём выбор темы (гейт)
    await expect(page.getByText(/Какую тему изучаем/).first()).toBeVisible({ timeout: 60000 })

    // 7) Кликаем первую тему
    await expect(page.locator('.topic-chip').first()).toBeVisible({ timeout: 30000 })
    const topicTitle = await page.locator('.topic-chip').first().textContent()
    console.log('Clicking topic:', topicTitle)
    await page.locator('.topic-chip').first().click()

    // 8) Ждём quiz card (или сообщение "Готовимся по теме")
    await page.waitForTimeout(3000)
    const quizCard = page.locator('.card.quiz')
    await expect(quizCard.first()).toBeVisible({ timeout: 90000 })
    console.log('Quiz card appeared!')

    // 9) Нет 500 ошибок
    expect(networkErrors).toEqual([])
  })
})
