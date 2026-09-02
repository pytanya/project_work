import { test, expect } from '@playwright/test'
import { fillIntakeCard } from './intake-helpers'

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

    // 1) Карточка знакомства
    await fillIntakeCard(page, {
      name: 'Ученик Четыре', learnerType: 'schoolchild', grade: '4',
      subject: 'основы православной культуры', topic: 'Культура и религия', hasTextbook: 'true', mode: 'quiz',
    })

    // 2) Агент просит загрузить файл
    await expect(page.getByText(/Загрузите, пожалуйста, файл учебника|Загрузите файл/).first()).toBeVisible({ timeout: 30_000 })

    // 3) Загружаем учебник
    await page.setInputFiles('input[type="file"]', OPK_PDF)

    // 4) Ждём индексацию
    await expect(page.getByText(/проиндексирован/).first()).toBeVisible({ timeout: 600_000 })

    // 5) Конкретная тема в intake → авто-выбор, квиз стартует сразу.
    //    Если гейт «Какую тему изучаем» всё же появился — выбираем первый чип.
    const gate = page.getByText(/Какую тему изучаем/).first()
    const quiz = page.locator('.card.quiz').first()
    try {
      await gate.waitFor({ state: 'visible', timeout: 30_000 })
      await page.locator('.topic-chip').first().click()
      await expect(page.getByRole('button', { name: /Изучить тему/ })).toBeVisible({ timeout: 15_000 })
      await page.getByRole('button', { name: /Изучить тему/ }).click()
    } catch { /* гейта нет — тема выбрана автоматически */ }

    // 6) Ждём quiz card
    await expect(quiz).toBeVisible({ timeout: 90_000 })
    console.log('Quiz card appeared!')

    // 7) Нет 500 ошибок
    expect(networkErrors).toEqual([])
  })
})
