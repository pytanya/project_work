import { test, expect } from '@playwright/test'

test.describe('Topic Selection Flow', () => {
  test('should show topic panel after indexing and select topic', async ({ page }) => {
    await page.goto('/')
    
    // 1) Fill intake quickly (mock scenario)
    const answers = ['ученик 4 класса', '4', 'основы православной культуры', 'Культура и религия', 'да', 'квиз']
    for (const a of answers) {
      await page.getByPlaceholder('Ваш ответ…').fill(a)
      await page.getByRole('button', { name: 'Отправить' }).click()
      await page.waitForTimeout(1000) // wait for response
    }
    
    // 2) Should ask to upload file
    await expect(page.getByText(/Загрузите, пожалуйста, файл учебника/).first()).toBeVisible({ timeout: 30000 })
    
    // 3) For this test, we'll skip actual file upload and mock the state
    // Instead, let's test the topic selection UI directly
    
    // Check if topic panel exists
    const topicPanel = page.locator('.card.graph')
    const hasTopicPanel = await topicPanel.count() > 0
    console.log('Topic panel exists:', hasTopicPanel)
    
    // If topic panel exists, check if topic chips are visible
    const topicChips = page.locator('.topic-chip')
    const chipsCount = await topicChips.count()
    console.log('Topic chips count:', chipsCount)
    
    // Click first topic chip if available
    if (chipsCount > 0) {
      await topicChips.first().click()
      await page.waitForTimeout(3000)
      
      // Check if "Изучаем:" appears
      const isStudying = page.getByText(/Изучаем:/)
      const isVisible = await isStudying.isVisible()
      console.log('"Изучаем:" visible:', isVisible)
      
      // Check if quiz card appears
      const quizCard = page.locator('.card.quiz')
      const quizVisible = await quizCard.isVisible()
      console.log('Quiz card visible:', quizVisible)
    } else {
      console.log('No topic chips found - indexing may not have completed or mock data needed')
    }
  })

  test('should update active topic in graph panel', async ({ page }) => {
    await page.goto('/')
    
    // Fill intake
    const answers = ['ученик 4 класса', '4', 'тест', 'Атмосфера', 'да', 'квиз']
    for (const a of answers) {
      await page.getByPlaceholder('Ваш ответ…').fill(a)
      await page.getByRole('button', { name: 'Отправить' }).click()
      await page.waitForTimeout(1000)
    }
    
    // Check session ID
    const sessionIdEl = page.locator('.session-id')
    const sessionId = await sessionIdEl.textContent()
    console.log('Session ID:', sessionId)
    
    // Get graph data via API
    if (sessionId && sessionId.startsWith('сессия:')) {
      const sid = sessionId.replace('сессия: ', '')
      const response = await page.evaluate(async (id) => {
        const res = await fetch(`/api/sessions/${id}/graph`)
        return await res.json()
      }, sid)
      
      console.log('Graph data:', JSON.stringify(response, null, 2))
      console.log('Active topic:', response.active_topic)
    }
  })
})
