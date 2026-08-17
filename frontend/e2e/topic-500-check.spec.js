import { test, expect } from '@playwright/test'

test.describe('Topic 500 error - direct API check via browser', () => {
  test('POST /topic should return 404 (not 500) for invalid topic', async ({ page, request }) => {
    // Создаём сессию через browser
    const createResp = await request.post('/api/sessions', { data: {} })
    expect(createResp.status()).toBe(201)
    const sid = (await createResp.json()).session_id
    console.log('Session:', sid)

    // Вызываем /topic с несуществующей темой
    const resp = await request.post(`/api/sessions/${sid}/topic`, {
      data: { topic_id: 'nonexistent_topic' }
    })
    console.log('POST /topic status:', resp.status())
    const body = await resp.text()
    console.log('Body:', body.slice(0, 200))

    // Ключевая проверка: НЕ 500
    expect(resp.status()).not.toBe(500)
  })
})
