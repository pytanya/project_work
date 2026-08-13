import { test, expect } from '@playwright/test'

const OPK_PDF = 'C:\\Users\\hppro\\OneDrive\\Рабочий стол\\учебники\\основы светской этики\\kuraev-osnovy-pravoslavnoy-kultury-uchebnik-dly-chetvertogo-klassa.pdf'

test('полный сценарий: intake → загрузка OPK → индекс → квиз', async ({ page }) => {
  test.setTimeout(420_000) // парсинг 96 стр. + эмбеддинги ~2-4 мин
  await page.goto('/')

  // 1) первый вопрос чек-листа (без дубля)
  await expect(page.getByText('Кто ты?').first()).toBeVisible({ timeout: 30_000 })

  // 2) интейк
  for (const a of ['ученик 4 класса', '4', 'основы православной культуры', 'да', 'квиз']) {
    await page.getByPlaceholder('Ваш ответ…').fill(a)
    await page.getByRole('button', { name: 'Отправить' }).click()
    // ждём, что текст появился в ленте
    await expect(page.locator('.bubble.user', { hasText: a }).last()).toBeVisible({ timeout: 15_000 })
  }

  // 3) агент просит загрузить файл (has_textbook=да, файла нет → wait_for_upload)
  await expect(page.getByText(/Загрузите, пожалуйста, файл учебника/).first()).toBeVisible({ timeout: 30_000 })

  // 4) загружаем учебник через input[type=file]
  await page.setInputFiles('input[type="file"]', OPK_PDF)

  // 5) видим прогресс индексации и завершение
  await expect(page.getByText(/Загружаю и индексирую/)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText(/проиндексирован/).first()).toBeVisible({ timeout: 240_000 })

  // 6) агент ждёт выбор темы (гейт после индексации)
  await expect(page.getByText(/Какую тему изучаем/).first()).toBeVisible({ timeout: 60_000 })
  await expect(page.locator('.topic-chip').first()).toBeVisible({ timeout: 30_000 })
  await page.locator('.topic-chip').first().click()

  // 6a) в панели виден выбранный урок, приходит первый вопрос квиза
  await expect(page.getByText(/Изучаем:/).first()).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.card.quiz')).toBeVisible({ timeout: 60_000 })

  // 7) отвечаем на вопрос
  const answerInput = page.getByPlaceholder('Ваш ответ…')
  await expect(answerInput).toBeEnabled({ timeout: 30_000 })
  await answerInput.fill('Я думаю, что это связано с православной культурой и религией России.')
  await page.getByRole('button', { name: 'Отправить' }).click()

  // 8) фидбек «Верно/Ошибка» + карта знаний обновилась
  await expect(page.locator('.bubble.agent').filter({ hasText: /Верно|Ошибка/ }).first()).toBeVisible({ timeout: 90_000 })
  await expect(page.locator('.card.progress .topic-bar').first()).toBeVisible({ timeout: 30_000 })

  // 9) в шапке видна сессия
  await expect(page.locator('.session-id').first()).toBeVisible({ timeout: 15_000 })
})
