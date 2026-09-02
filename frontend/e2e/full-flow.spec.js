import { test, expect } from '@playwright/test'
import { fillIntakeCard } from './intake-helpers'

const OPK_PDF = 'C:\\Users\\hppro\\OneDrive\\Рабочий стол\\учебники\\основы светской этики\\kuraev-osnovy-pravoslavnoy-kultury-uchebnik-dly-chetvertogo-klassa.pdf'

test('полный сценарий: intake → загрузка OPK → индекс → квиз', async ({ page }) => {
  test.setTimeout(900_000) // парсинг 96 стр. + эмбеддинги: API провайдера бывает медленным
  await page.goto('/')

  // 1) Карточка знакомства (форма вместо пошаговых вопросов)
  await fillIntakeCard(page, {
    name: 'Ученик Четыре', learnerType: 'schoolchild', grade: '4',
    subject: 'основы православной культуры', topic: 'Культура и религия', hasTextbook: 'true', mode: 'quiz',
  })

  // 2) агент просит загрузить файл (has_textbook=да, файла нет → wait_for_upload)
  await expect(page.getByText(/Загрузите, пожалуйста, файл учебника|Загрузите файл/).first()).toBeVisible({ timeout: 30_000 })

  // 3) загружаем учебник через input[type=file]
  await page.setInputFiles('input[type="file"]', OPK_PDF)

  // 4) видим прогресс индексации и завершение
  await expect(page.getByText(/Загружаю и индексирую/)).toBeVisible({ timeout: 60_000 })
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

  // 6) приходит первый вопрос квиза
  await expect(quiz).toBeVisible({ timeout: 90_000 })

  // 7) отвечаем: для single-choice — клик по первому варианту, иначе свободный ответ
  const option = page.locator('.card.quiz .option, .quiz-card .option').first()
  if (await option.count()) {
    await option.click()
  } else {
    const answerInput = page.getByPlaceholder('Ваш ответ…')
    await expect(answerInput).toBeEnabled({ timeout: 30_000 })
    await answerInput.fill('Я думаю, что это связано с православной культурой и религией России.')
    await page.getByRole('button', { name: 'Отправить' }).click()
  }

  // 8) ответ обработан: фидбек «Верно/Ошибка» ИЛИ подсказка (scaffolding)
  await expect(
    page.locator('.bubble').filter({ hasText: /Верно \(оценка|Ошибка \(оценка|Подсказка/ }).first()
  ).toBeVisible({ timeout: 300_000 })

  // 9) в шапке видна сессия
  await expect(page.locator('.session-id')).toContainText(/сессия:/, { timeout: 30_000 })
})
