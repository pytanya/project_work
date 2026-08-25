// EduTutor — клиент REST + WebSocket (раздел 8).
// VITE_API_BASE — если фронтенд и бэкенд на разных хостах (иначе proxy Vite).

const BASE = import.meta.env.VITE_API_BASE || ''

async function jsonFetch(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    // FastAPI отвечает {detail: "..."} — достаём человекочитаемое сообщение.
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') detail = body.detail
      else if (Array.isArray(body?.detail)) detail = body.detail.map((d) => d.msg).join('; ')
    } catch (_) { /* не-JSON ответ — оставляем HTTP-статус */ }
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  // student_id — стабильный профиль ученика (localStorage); без него бэкенд создаёт новый
  createSession: (studentId, initial) =>
    jsonFetch('/api/sessions', {
      method: 'POST',
      body: JSON.stringify({ student_id: studentId || undefined, initial: initial || {} }),
    }),

  getSession: (id) => jsonFetch(`/api/sessions/${id}`),

  deleteSession: (id) => fetch(`${BASE}/api/sessions/${id}`, { method: 'DELETE' }),

  intakeStatus: (id) => jsonFetch(`/api/sessions/${id}/intake/status`),

  postIntake: (id, answer) =>
    jsonFetch(`/api/sessions/${id}/intake`, { method: 'POST', body: JSON.stringify({ answer }) }),

  // Карточка знакомства: все поля чек-листа сразу (быстрое заполнение)
  postIntakeCard: (id, values) =>
    jsonFetch(`/api/sessions/${id}/intake/card`, { method: 'POST', body: JSON.stringify({ values }) }),

  postMessage: (id, text) =>
    jsonFetch(`/api/sessions/${id}/message`, { method: 'POST', body: JSON.stringify({ text }) }),

  uploadFile: async (id, file) => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE}/api/sessions/${id}/upload`, { method: 'POST', body: form })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    return res.json()
  },

  findTextbook: (id, opts = {}) =>
    jsonFetch(`/api/sessions/${id}/find-textbook`, { method: 'POST', body: JSON.stringify(opts) }),

  sourceStatus: (id) => jsonFetch(`/api/sessions/${id}/source-status`),

  getGraph: (id) => jsonFetch(`/api/sessions/${id}/graph`),

  selectTopic: (id, topicId) =>
    jsonFetch(`/api/sessions/${id}/topic`, { method: 'POST', body: JSON.stringify({ topic_id: topicId }) }),

  history: (id) => jsonFetch(`/api/sessions/${id}/history`),

  getStudentSessions: (studentId) =>
    jsonFetch(`/api/students/${encodeURIComponent(studentId)}/sessions`),
}

export function wsUrl(sessionId) {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/api/sessions/${sessionId}/ws`
}
