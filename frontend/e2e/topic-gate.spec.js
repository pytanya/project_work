import { test, expect } from '@playwright/test'

// E2E гейта выбора темы: фронтенд полностью замокан (page.route + routeWebSocket),
// никаких живых LLM/эмбеддингов — детерминированно.
const GRAPH = {
  nodes: [
    { id: 'book:opk', title: 'Учебник «ОПК»', type: 'book', color: '#F4A261' },
    { id: 't:1', title: 'Урок 1: Россия — наша Родина', type: 'topic', color: '#64DFDF' },
    { id: 't:2', title: 'Урок 2: Культура и религия', type: 'topic', color: '#B388FF' },
    { id: 't:3', title: 'Урок 3: Человек и Бог в православии', type: 'topic', color: '#FFD166' },
  ],
  edges: [
    { source: 'book:opk', target: 't:1', relation: 'part_of' },
    { source: 'book:opk', target: 't:2', relation: 'part_of' },
    { source: 'book:opk', target: 't:3', relation: 'part_of' },
  ],
}

function state(db) {
  return {
    learner_type: 'schoolchild',
    grade: '4',
    subject: 'основы православной культуры',
    has_textbook: true,
    mode: 'quiz',
    source_status: 'ready',
    source_note: 'Документ проиндексирован: 42 чанков',
    awaiting_topic: true,
    active_topic: db.activeTopic || null,
    knowledge_graph: GRAPH,
    knowledge_map: {},
    correct_count: 0,
    answered_count: 0,
    current_question: db.currentQuestion || null,
    intake_field: null,
    agent_question: db.agentQuestion || null,
    missing_fields: [],
  }
}

async function mockApi(page, db) {
  await page.routeWebSocket('**/api/sessions/*/ws', (ws) => {
    // НЕ форвардим на реальный бэкенд (у него нет mock-session → 4004 «Сессия не найдена»).
    // Имитируем backend: когда HTTP POST /topic пометил db.topicSelected —
    // шлём quiz.card (фронтенд ждёт его по WS, чтобы показать карточку квиза).
    const timer = setInterval(() => {
      if (db.topicSelected && !db.quizSent) {
        db.quizSent = true
        const node = GRAPH.nodes.find((n) => n.id === db.activeTopic)
        ws.send(JSON.stringify({
          event: 'quiz.card',
          data: {
            question_id: 'q1',
            question: `Вопрос по теме: ${node?.title || 'тема'}`,
            options: ['А', 'Б', 'В'],
            answer_type: 'single',
            difficulty: 'easy',
            topic: node?.title || 'тема',
          },
        }))
        clearInterval(timer)
      }
    }, 100)
    ws.onClose(() => clearInterval(timer))
  })
  await page.route('**/api/sessions**', async (route) => {
    const req = route.request()
    const url = new URL(req.url())
    const method = req.method()
    const id = url.pathname.split('/')[3] || ''
    const isGraph = url.pathname.endsWith('/graph')
    const isTopic = url.pathname.endsWith('/topic')
    const isIntakeStatus = url.pathname.endsWith('/intake/status')
    const isSourceStatus = url.pathname.endsWith('/source-status')

    if (method === 'POST' && url.pathname === '/api/sessions') {
      db.id = 'mock-session'
      return route.fulfill({ status: 201, json: { session_id: db.id } })
    }
    if (method === 'GET' && isGraph) {
      return route.fulfill({ json: { ...GRAPH, active_topic: db.activeTopic || null } })
    }
    if (method === 'GET' && isIntakeStatus) {
      return route.fulfill({ json: { complete: true, missing_fields: [] } })
    }
    if (method === 'GET' && isSourceStatus) {
      return route.fulfill({ json: { status: 'ready', note: 'Документ проиндексирован: 42 чанков' } })
    }
    if (method === 'GET' && url.pathname.endsWith(`/api/sessions/${id}`)) {
      return route.fulfill({ json: state(db) })
    }
    if (method === 'POST' && isTopic) {
      const body = req.postDataJSON()
      db.activeTopic = body.topic_id
      db.topicSelected = true  // сигнал WS-моку: пора слать quiz.card
      const node = GRAPH.nodes.find((n) => n.id === body.topic_id)
      db.currentQuestion = {
        question_id: 'q1',
        question: `Вопрос по теме: ${node?.title || 'тема'}`,
        options: ['А', 'Б', 'В'],
        answer_type: 'single',
        difficulty: 'easy',
        topic: node?.title || 'тема',
      }
      db.agentQuestion = null
      return route.fulfill({
        json: { ok: true, active_topic: body.topic_id, title: node?.title, question: db.currentQuestion },
      })
    }
    if (method === 'POST' && url.pathname.endsWith(`/api/sessions/${id}/intake`)) {
      return route.fulfill({ json: { complete: true, missing_fields: [] } })
    }
    if (method === 'POST' && url.pathname.endsWith(`/api/sessions/${id}/message`)) {
      return route.fulfill({ json: { type: 'system', payload: {} } })
    }
    if (method === 'DELETE') {
      return route.fulfill({ status: 204 })
    }
    return route.continue()
  })
}

test('после индексации: панель тем (SVG+чипы) → клик по уроку → карточка квиза', async ({ page }) => {
  const db = { activeTopic: null, currentQuestion: null, agentQuestion: null, topicSelected: false, quizSent: false }
  await mockApi(page, db)
  await page.goto('/')

  // 1) граф пришёл — панель тем видна
  await expect(page.getByText(/Граф знаний · 3/)).toBeVisible({ timeout: 20_000 })

  // 2) кликаем тему из списка чипов → открывается карточка → «Изучить тему»
  await page.getByRole('button', { name: 'Урок 2: Культура и религия' }).click()
  await expect(page.getByRole('button', { name: /Изучить тему/ })).toBeVisible({ timeout: 15_000 })
  await page.getByRole('button', { name: /Изучить тему/ }).click()

  // 3) выбранный урок подсвечен, появилась карточка квиза
  await expect(page.getByText(/Изучаем:/).first()).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.card.quiz')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText(/Вопрос по теме: Урок 2: Культура и религия/).first()).toBeVisible()

  // 4) фильтр по поиску работает
  await page.getByPlaceholder('Найти тему…').fill('Бог')
  await expect(page.getByRole('button', { name: 'Урок 3: Человек и Бог в православии' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Урок 1: Россия — наша Родина' })).toHaveCount(0)
})
