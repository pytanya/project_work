import { useCallback, useEffect, useRef, useState } from 'react'
import { api, wsUrl } from './api'
import ChatStream from './components/ChatStream'
import IntakeWizard from './components/IntakeWizard'
import IntakeCard from './components/IntakeCard'
import QuizCard from './components/QuizCard'
import SourceSearchPanel from './components/SourceSearchPanel'
import FileUpload from './components/FileUpload'
import KnowledgeGraphPanel from './components/KnowledgeGraphPanel'
import KnowledgeWikiPanel from './components/KnowledgeWikiPanel'
import SessionHistoryPanel from './components/SessionHistoryPanel'
import SourceWhitelistPanel from './components/SourceWhitelistPanel'
import './index.css'

const STORAGE_KEY = 'edututor_settings'
const STUDENT_KEY = 'edututor_student'
const SIDEBAR_KEY = 'edututor_sidebar_width'
const SIDEBAR_MIN = 260
const SIDEBAR_MAX = 560

function loadSettings() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY)) || {}
  } catch {
    return {}
  }
}

function saveSettings(s) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(s))
}

function loadStudent() {
  try {
    return JSON.parse(localStorage.getItem(STUDENT_KEY)) || {}
  } catch {
    return {}
  }
}

function saveStudent(s) {
  localStorage.setItem(STUDENT_KEY, JSON.stringify(s))
}

function loadSidebarWidth() {
  const v = Number(localStorage.getItem(SIDEBAR_KEY))
  if (Number.isFinite(v) && v >= SIDEBAR_MIN && v <= SIDEBAR_MAX) return v
  return 320
}

export default function App() {
  const [sessionId, setSessionId] = useState(null)
  const [student, setStudent] = useState(() => loadStudent())
  const [feed, setFeed] = useState([])
  const [current, setCurrent] = useState(null)
  const [intake, setIntake] = useState({ missingFields: [], complete: false })
  const [source, setSource] = useState({ status: null, note: null, sources: [], author: null, textbookUrl: '' })
  const [graph, setGraph] = useState({ nodes: [], edges: [], activeTopic: null })
  const [knowledge, setKnowledge] = useState({})
  const [wikiReloadKey, setWikiReloadKey] = useState(0)
  const [sessionHistoryReloadKey, setSessionHistoryReloadKey] = useState(0)
  // Политика источников: белый список + «любые источники» (пер-студентная)
  const [sourcePolicy, setSourcePolicy] = useState({ allow_any_sources: true, whitelist: [] })
  const [sourcePanelSignal, setSourcePanelSignal] = useState(0)
  const [sourceProposal, setSourceProposal] = useState(null)
  const [score, setScore] = useState({ correct: 0, total: 0 })
  const [quizCount, setQuizCount] = useState(0)
  const [answer, setAnswer] = useState('')
  const [confirmedOption, setConfirmedOption] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [quickAnswer, setQuickAnswer] = useState(() => loadSettings().quickAnswer !== false)
  // Ширина боковой панели (ресайз перетаскиванием, сохраняется в localStorage)
  const [sidebarWidth, setSidebarWidth] = useState(() => loadSidebarWidth())
  const sidebarDragRef = useRef(null)
  // Разделение busy: upload для индексации чат для квиза/сообщений
  const [uploadBusy, setUploadBusy] = useState(false)
  const [chatBusy, setChatBusy] = useState(false)
  // Контекстный прогресс подготовки урока/вопроса: {stage, message, status}
  const [progressPhase, setProgressPhase] = useState(null)
  // Счётчик вопросов квиза из записей
  const [questionNum, setQuestionNum] = useState(0)
  const wsRef = useRef(null)
  const sessionIdRef = useRef(null)
  const inputRef = useRef(null)
  const settingsBtnRef = useRef(null)
  const currentRef = useRef(null)          // зеркало current для обработчиков WS (без stale-closure)
  const sourcePolicyRef = useRef({ allow_any_sources: true, whitelist: [] })
  const streamRef = useRef(null)           // id «живого» пузыря со стримингом токенов
  // Refs: отслеживаем ожидаем ли результат ответа на текущий вопрос
  const pendingAnswer = useRef(null)       // текст отправленного ответа
  const currentKindAtSubmit = useRef(null) // kind экрана в момент отправки
  const isWaitingForAnswer = useRef(false) // флаг: ждём WS событие после отправки
  const isPreparingTopic = useRef(false)   // флаг: идёт фоновая подготовка темы (fire-and-forget)
  const answerTimeoutRef = useRef(null)    // id busy-таймаута (сбрасывается heartbeat'ом)

  // Синхронизируем currentRef с current (для handleEvent, у которого stable-замыкание)
  useEffect(() => {
    currentRef.current = current
  }, [current])

  // Политика источников — тоже зеркалим в ref (stable-замыкание handleEvent)
  useEffect(() => {
    sourcePolicyRef.current = sourcePolicy
  }, [sourcePolicy])

  // Политика источников: подгружаем при смене ученика
  useEffect(() => {
    if (!student.student_id) return
    let cancelled = false
    api.getSourcePolicy(student.student_id)
      .then((p) => !cancelled && setSourcePolicy(p))
      .catch(() => {})
    return () => { cancelled = true }
  }, [student.student_id])

  // Загрузка настроек из localStorage при старте
  useEffect(() => {
    const stored = loadSettings()
    if (stored.quickAnswer !== undefined) {
      setQuickAnswer(stored.quickAnswer !== false)
    }
  }, [])

  // Сохранение настроек
  useEffect(() => {
    saveSettings({ quickAnswer })
  }, [quickAnswer])

  // Focus после завершения операций на чате + закрыть по клику вне settings
  useEffect(() => {
    if (!chatBusy && !uploadBusy) {
      const t = setTimeout(() => inputRef.current?.focus(), 80)
      return () => clearTimeout(t)
    }
  }, [chatBusy, uploadBusy])

  useEffect(() => {
    function handleClick(e) {
      if (settingsBtnRef.current && !settingsBtnRef.current.contains(e.target)) {
        setSettingsOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [])

  const push = useCallback((kind, text, data) => {
    setFeed((f) => {
      // Глобальная проверка на дубли по тексту (исправляет повторение одного сообщения разными kind)
      const normalized = text.trim().toLowerCase()
      const alreadyExists = f.some((m) => (m.text || '').trim().toLowerCase() === normalized)
      if (alreadyExists) return f
      return [...f, { id: `${Date.now()}-${Math.random()}`, kind, text, data }]
    })
  }, [])

  // Реальный стриминг токенов (stream=True): токены накапливаются в одном «живом» пузыре.
  const pushToken = useCallback((text) => {
    setFeed((f) => {
      if (streamRef.current) {
        return f.map((m) => (m.id === streamRef.current ? { ...m, text: (m.text || '') + text } : m))
      }
      const id = `stream-${Date.now()}-${Math.random()}`
      streamRef.current = id
      return [...f, { id, kind: 'stream', text, data: {} }]
    })
  }, [])

  // Завершение стриминга: финальное событие завершает «живой» пузырь.
  const endStream = useCallback(() => {
    streamRef.current = null
  }, [])

  // Финализация стрима: превращаем «живой» пузырь токенов в финальный вид (kind/text).
  // Иначе остаётся мигающая каретка + дубль-пузырь не проходит по dedupe (тот же текст).
  const finalizeStream = useCallback((kind, text, data) => {
    setFeed((f) => {
      const id = streamRef.current
      streamRef.current = null
      if (id) {
        return f.map((m) => (m.id === id ? { ...m, kind, text, data } : m))
      }
      const normalized = String(text || '').trim().toLowerCase()
      const alreadyExists = f.some((m) => (m.text || '').trim().toLowerCase() === normalized)
      if (alreadyExists) return f
      return [...f, { id: `${Date.now()}-${Math.random()}`, kind, text, data }]
    })
  }, [])

  // Busy-таймаут (оптимизация #5): 120 сек вместо 240. Каждый WS-событие heartbeat
  // (system.heartbeat) продлевает таймаут, поэтому долгая генерация не «отваливается».
  const ANSWER_TIMEOUT = 120000
  const resetBusyAfterTimeout = useCallback(() => {
    if (answerTimeoutRef.current) clearTimeout(answerTimeoutRef.current)
    answerTimeoutRef.current = setTimeout(() => {
      if (isWaitingForAnswer.current || isPreparingTopic.current) {
        setChatBusy(false)
        setProgressPhase(null)
        isWaitingForAnswer.current = false
        isPreparingTopic.current = false
      }
    }, ANSWER_TIMEOUT)
  }, [])

  const refreshGraph = useCallback(async () => {
    if (!sessionIdRef.current) return
    try {
      const g = await api.getGraph(sessionIdRef.current)
      setGraph({ nodes: g.nodes || [], edges: g.edges || [], activeTopic: g.active_topic || null })
    } catch (_) {
      /* граф может быть ещё не построен — тихо */
    }
  }, [])

  const handleEvent = useCallback(
    (evt) => {
      const d = evt.data || {}
      
      // Сбрасываем busy если мы ожидали ответ на предыдущий вопрос
      // и получили ФИНАЛЬНОЕ событие шага. Промежуточные (source.progress,
      // system kind="intent") НЕ сбрасывают busy — иначе индикатор «раздумий»
      // пропадает, хотя генерация ещё идёт.
      // НО не во время фоновой подготовки темы: там busy держит isPreparingTopic,
      // иначе событие `system` от select_topic сбросит индикатор раньше времени.
      if (isWaitingForAnswer.current && !isPreparingTopic.current) {
        const answerResolvedEvents = [
          'quiz.card', 'tutor.lesson', 'tutor.explanation',
          'tutor.summary', 'intake.question', 'intake.card', 'source.failed', 'session.error'
        ]
        if (answerResolvedEvents.includes(evt.event)) {
          setChatBusy(false)
          isWaitingForAnswer.current = false
        }
        // `system` сбрасывает busy только когда это финал шага (не intent/warning):
        // topic.all / topic.selected / lesson.done / lesson.repeat / lesson.ready /
        // agent.message / doc.scanned.
        if (evt.event === 'system') {
          const finalSystemKinds = [
            'topic.all', 'topic.selected', 'lesson.done', 'lesson.repeat',
            'lesson.ready', 'agent.message', 'doc.scanned', 'content.empty',
            'lesson.judge'
          ]
          if (finalSystemKinds.includes(d.kind)) {
            setChatBusy(false)
            isWaitingForAnswer.current = false
          }
        }
      }

      // Fire-and-forget подготовка темы: busy держим до финального события
      // (вопрос/урок/сводка), а прогресс-события обновляют progressPhase.
      if (isPreparingTopic.current) {
        const finalEvents = [
          'quiz.card', 'tutor.lesson', 'tutor.summary', 'intake.question',
          'source.failed', 'session.error'
        ]
        if (finalEvents.includes(evt.event)) {
          isPreparingTopic.current = false
          setChatBusy(false)
          setProgressPhase(null)
        }
      }
      
      switch (evt.event) {
        case 'token':
          pushToken(d.text)
          break
        case 'intake.question':
          finalizeStream('intake', d.question)
          setCurrent({ kind: 'intake', question: d.question, missingFields: d.missing_fields, options: d.options })
          break
        case 'intake.card':
          // Карточка знакомства: форма вместо пошаговых вопросов (быстрое заполнение)
          finalizeStream('intake', d.question)
          setCurrent({ kind: 'intake_card', card: d.card, question: d.question })
          break
        case 'quiz.card':
          finalizeStream('quiz', d.question)
          setCurrent({
            kind: 'quiz',
            question: d.question,
            options: d.options,
            answerType: d.answer_type,
            topic: d.topic,
            difficulty: d.difficulty,
            questionId: d.question_id,
            excerpt: d.excerpt || '',
          })
          break
        case 'tutor.explanation':
          finalizeStream('explanation', d.message, d)
          setCurrent(null)
          break
        case 'tutor.lesson':
          finalizeStream('lesson', d.text, { topic: d.topic, lesson: d.lesson })
          break
        case 'tutor.summary':
          finalizeStream('summary', `Квиз завершён: правильных ${d.correct}/${d.total}`)
          setCurrent(null)
          setKnowledge(d.knowledge_map || {})
          setScore({ correct: d.correct || 0, total: d.total || 0 })
          setQuizCount(d.total || 0)
          setQuestionNum(d.total || 0)
          // Квиз завершён — освежаем историю занятий ученика
          setSessionHistoryReloadKey((k) => k + 1)
          break
        case 'source.progress':
          endStream()
          setSource({ status: d.status, note: d.message })
          // Фаза источника = intake уже завершён: баннер чек-листа сбрасываем,
          // активную карточку квиза не трогаем.
          const cProg = currentRef.current
          if (!cProg || cProg.kind !== 'quiz') {
            setCurrent(null)
          }
          push('source', d.message)
          // Гранулярный прогресс при подготовке темы (оптимизация #1)
          if (isPreparingTopic.current && d.message && d.status !== 'done' && d.status !== 'ready') {
            setProgressPhase({ stage: d.stage, message: d.message, status: d.status })
          }
          break
        case 'system.heartbeat':
          // Heartbeat: продлеваем busy-таймаут + обновляем контекст прогресса.
          // elapsed (сек) показываем в сообщении — пользователь видит, что генерация идёт.
          resetBusyAfterTimeout()
          if (isPreparingTopic.current) {
            setProgressPhase((p) => ({
              stage: p?.stage || 'topic',
              message: `${d.message || p?.message || 'Обработка продолжается…'}${
                d.elapsed ? ` (${d.elapsed} сек)` : ''
              }`,
              status: 'working',
            }))
          }
          break
        case 'source.failed':
          endStream()
          setSource({ status: 'failed', note: d.message })
          const cFail = currentRef.current
          if (!cFail || cFail.kind !== 'quiz') {
            setCurrent(null)
          }
          push('error', d.message)
          // Белый список: поиск не нашёл ничего по разрешённым доменам —
          // предлагаем включить любые источники или изменить список.
          if (d.reason === 'whitelist_blocked') {
            setSourceProposal({ type: 'whitelist_blocked', message: d.message })
          }
          break
        case 'graph.ready':
          refreshGraph()
          push('system', `Построен граф знаний: ${d.nodes} тем`)
          break
        case 'wiki.updated':
          setWikiReloadKey((k) => k + 1)
          refreshGraph()  // мастерство узлов графа обновляется после квиза (а не только на graph.ready)
          break
        case 'system':
          endStream()
          // Информационные события (фоновый судья, RAG-гейт, агент-сообщения)
          // НЕ должны сносить активный UI (карточка квиза / чек-лист).
          if (!['lesson.judge', 'content.empty', 'agent.message'].includes(d.kind)) {
            setCurrent(null)
          }
          push('system', d.message)
          // Белый список активен, а материала в разрешённых источниках нет —
          // предлагаем включить любые источники.
          if (d.kind === 'content.empty' && sourcePolicyRef.current && !sourcePolicyRef.current.allow_any_sources) {
            setSourceProposal({ type: 'content_empty', message: d.message })
          }
          break
        case 'intake.completed':
          // Исправление #3: обработка завершения intake процесса
          // После сбора всей информации начинаем квиз
          push('system', 'Информация собрана, начинаем квив...')
          break
        case 'session.error':
          push('error', d.message)
          break
        default:
          break
      }
    },
    [push, refreshGraph, pushToken, finalizeStream, endStream, resetBusyAfterTimeout],
  )

  useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])

  useEffect(() => {
    let cancelled = false
    let reconnectAttempts = 0
    let reconnectTimer = null

    const connectWs = (sid) => {
      // Защита от дублей: не создаём второе подключение, если живое уже есть/устанавливается.
      const cur = wsRef.current
      if (cur && (cur.readyState === WebSocket.OPEN || cur.readyState === WebSocket.CONNECTING)) {
        return cur
      }
      const ws = new WebSocket(wsUrl(sid))
      ws.onmessage = (e) => {
        try {
          handleEvent(JSON.parse(e.data))
        } catch (_) {}
      }
      ws.onopen = () => {
        reconnectAttempts = 0
      }
      // auto-reconnect: бэкенд мог перезапуститься (WS закрылся аномально) — переподключаемся.
      // Код 1000 = штатное закрытие сервером (idle-таймаут сессии) — переподключение не нужно.
      ws.onclose = (ev) => {
        if (cancelled || !sessionIdRef.current) return
        if (ev.code === 1000) return
        if (reconnectAttempts < 6) {
          reconnectAttempts += 1
          reconnectTimer = setTimeout(() => {
            wsRef.current = connectWs(sessionIdRef.current)
          }, 3000 * reconnectAttempts)
        }
      }
      wsRef.current = ws
      return ws
    }

    async function init() {
      try {
        // retry: бэкенд может быть ещё на старте (Qdrant/embedder ~15с) — переживаем
        let sessionId = null
        for (let attempt = 1; attempt <= 5; attempt++) {
          try {
            // Стабильный профиль ученика: сессии одного ученика делят Wiki/мастерство/заметки
            const r = await api.createSession(student.student_id)
            sessionId = r.session_id
            if (r.student_id !== student.student_id || r.student_name !== student.student_name) {
              setStudent({ student_id: r.student_id, student_name: r.student_name || '' })
              saveStudent({ student_id: r.student_id, student_name: r.student_name || '' })
            }
            break
          } catch (e) {
            if (cancelled) return
            if (attempt === 5) throw e
            await new Promise((resolve) => setTimeout(resolve, 2000 * attempt))
          }
        }
        if (cancelled || !sessionId) return
        setSessionId(sessionId)
        connectWs(sessionId)
        const st = await api.intakeStatus(sessionId)
        setIntake({ missingFields: st.missing_fields, complete: st.complete })
        // Карточка знакомства (agent_card) имеет приоритет: ждёт заполнения формы.
        // getSession — авторитетный снимок (WS мог ещё не успеть прислать intake.card).
        const sess = await api.getSession(sessionId)
        if (sess.agent_card) {
          setCurrent({ kind: 'intake_card', card: sess.agent_card, question: sess.agent_question })
        } else if (!st.complete && st.next_question) {
          setCurrent({ kind: 'intake', question: st.next_question, missingFields: st.missing_fields, options: sess.agent_options || [] })
          push('intake', st.next_question)
        }
        refreshGraph()
      } catch (e) {
        push('error', `Не удалось создать сессию: ${e.message}. Проверьте, что бэкенд запущен (uvicorn api.app:app --port 8000).`)
      }
    }
    init()
    const unload = () => {
      if (sessionIdRef.current) api.deleteSession(sessionIdRef.current)
    }
    window.addEventListener('beforeunload', unload)
    return () => {
      cancelled = true
      window.removeEventListener('beforeunload', unload)
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (wsRef.current) wsRef.current.close()
    }
  }, [handleEvent, push])

  // resync: подтягиваем актуальное состояние сессии по HTTP
  const resync = useCallback(async () => {
    if (!sessionIdRef.current) return
    try {
      const d = await api.getSession(sessionIdRef.current)
      
      // Исправление #2: защита resync от перезаписи current
      // Если фронтенд уже показывает активный вопрос (quiz/intake/card), не перезаписываем current
      const hasFrontendActiveQuestion = current && (current.kind === 'quiz' || current.kind === 'intake' || current.kind === 'intake_card')
      
      // Восстанавливаем current только если бэкенд имеет неотображённый вопрос
      // И фронтенд в данный момент ничего не показывает (чтобы не потерять UI)
      if (d.current_question && !hasFrontendActiveQuestion) {
        const q = d.current_question
        setCurrent({
          kind: 'quiz', question: q.question, options: q.options, answerType: q.answer_type,
          topic: q.topic, difficulty: q.difficulty, questionId: q.question_id,
        })
        // Обновляем счётчик вопросов из records
        if (d.records && d.records.length > 0) {
          setQuestionNum(d.records.filter(r => r.student_answer).length)
          setQuizCount(d.num_questions || 10)
        }
      } else if (d.agent_card && !hasFrontendActiveQuestion) {
        // Карточка знакомства ждёт заполнения
        setCurrent({ kind: 'intake_card', card: d.agent_card, question: d.agent_question })
      } else if (d.agent_question && !hasFrontendActiveQuestion) {
        setCurrent({ kind: 'intake', question: d.agent_question, missingFields: d.missing_fields || [], options: d.agent_options || [] })
      }
      // else: оставляем текущий current без изменений если фронтенд уже показывает вопрос

      // 7.3.3: урок/разбор не теряется при resync (WS мог переподключиться).
      // Отдаём lesson_text из состояния, если он ещё не показан в фиде.
      if (d.lesson_text && !hasFrontendActiveQuestion) {
        const alreadyShown = feed.some((m) => m.kind === 'lesson' && m.text === d.lesson_text)
        if (!alreadyShown) {
          push('lesson', d.lesson_text, {
            topic: d.active_topic || d.topic || '',
            lesson: {
              title: d.lesson_title,
              hook: d.lesson_hook,
              definition: d.lesson_definition,
              key_terms: d.lesson_key_terms || [],
              diagram: d.lesson_diagram || null,
              sections: d.lesson_sections || [],
              summary: d.lesson_summary,
              eval: d.lesson_eval || null,
            },
          })
        }
      }
      
      // Захватываем поля intake-фазы для отображения в чек-листе
      setIntake((prev) => ({
        ...prev,
        learner_type: d.learner_type ?? prev.learner_type,
        grade: d.grade ?? prev.grade,
        subject: d.subject ?? prev.subject,
        topic: d.topic ?? prev.topic,
        has_textbook: d.has_textbook ?? prev.has_textbook,
        chapter: d.chapter ?? prev.chapter,
        mode: d.mode ?? prev.mode,
      }))
      setKnowledge(d.knowledge_map || {})
      setScore({ correct: d.correct_count || 0, total: d.answered_count || 0 })
      if (d.source_status) {
        // Извлекаем URL из sources (sources[0].url или sources[0].path)
        const sourceUrl = (d.sources && d.sources.length > 0)
          ? (d.sources[0].url || d.sources[0].path)
          : ''
        setSource({
          status: d.source_status,
          note: d.source_note,
          sources: d.sources || [],
          author: d.textbook_author || null,
          textbookUrl: d.textbook_url || d.textbook_file || sourceUrl,
        })
      }
      if (d.knowledge_graph && d.knowledge_graph.nodes) {
        setGraph({ nodes: d.knowledge_graph.nodes, edges: d.knowledge_graph.edges || [], activeTopic: d.active_topic || null })
      }
    } catch (e) {
      push('error', String(e.message || e))
    }
  }, [push, feed])

  async function sendMessage(text) {
    if (!text || !sessionId) return
    setAnswer('')
    setConfirmedOption(null)
    push('user', text)
    setChatBusy(true)
    isWaitingForAnswer.current = true  // помечаем что ждём WS событие от бэкенда

    // Таймаут fallback: если WS событие не пришло за 120 секунд — сбрасываем busy.
    // Heartbeat-события (system.heartbeat) продлевают таймаут при долгой генерации.
    resetBusyAfterTimeout()

    try {
      const isIntakeTurn = current?.kind === 'intake' || current?.kind === 'intake_card'
      if (isIntakeTurn) {
        await api.postIntake(sessionId, text)
      } else {
        await api.postMessage(sessionId, text)
      }
      // intakeStatus — только на ходах чек-листа (иначе лишний GET после ответа на квиз).
      // WS события уже обновляют UI (quiz.card, tutor.explanation, system).
      if (isIntakeTurn) {
        const st = await api.intakeStatus(sessionId)
        setIntake({ missingFields: st.missing_fields, complete: st.complete })
        // Баннер чек-листа: intake завершён → снимаем вопрос (дальше идут source/урок/квиз)
        if (st.complete && currentRef.current?.kind === 'intake') {
          setCurrent(null)
        }
      }
    } catch (e) {
      push('error', String(e.message || e))
      setChatBusy(false)
      isWaitingForAnswer.current = false
    } finally {
      if (answerTimeoutRef.current) clearTimeout(answerTimeoutRef.current)
      // busy сбросится при получении WS события или по timeout
    }
  }

  async function submitAnswer() {
    sendMessage(answer.trim())
  }

  async function submitIntakeCard(values) {
    if (!sessionId) return
    push('intake', 'Карточка знакомства заполнена ✓')
    setChatBusy(true)
    isWaitingForAnswer.current = true
    resetBusyAfterTimeout()
    try {
      await api.postIntakeCard(sessionId, values)
      // Профиль ученика обновляем локально (имя) — для шапки/панели
      if (values.name) {
        const next = { ...student, student_name: values.name }
        setStudent(next)
        saveStudent(next)
      }
      const st = await api.intakeStatus(sessionId)
      setIntake({ missingFields: st.missing_fields, complete: st.complete })
      if (st.complete) {
        setCurrent(null)
      } else if (st.next_question) {
        setCurrent({ kind: 'intake', question: st.next_question, missingFields: st.missing_fields })
      }
    } catch (e) {
      push('error', String(e.message || e))
    } finally {
      if (answerTimeoutRef.current) clearTimeout(answerTimeoutRef.current)
    }
  }

  const onOption = (opt) => {
    setAnswer(opt)
    if (quickAnswer) {
      setConfirmedOption(null)
      sendMessage(opt)
    } else {
      setConfirmedOption(opt)
    }
  }

  async function handleUpload(file) {
    if (!sessionId) return
    setUploadBusy(true)
    setSource({ status: 'indexing', note: `Загружаю «${file.name}»…` })
    push('system', `Загружаю и индексирую «${file.name}», это может занять 1-2 минуты…`)
    try {
      const r = await api.uploadFile(sessionId, file)
      if (r.status === 'failed') {
        push('error', r.note || 'Не удалось проиндексировать документ.')
        setSource({ status: 'failed', note: r.note || 'Ошибка индексации' })
      } else {
        push('system', `Файл «${r.filename}» ${r.status === 'ready' ? 'проиндексирован' : 'принят'}`)
        if (r.status === 'ready') setSource({ status: 'ready', note: r.note })
      }
      await resync()
    } catch (e) {
      push('error', String(e.message || e))
      setSource({ status: 'failed', note: String(e.message || e) })
    } finally {
      setUploadBusy(false)
    }
  }

  async function handleFind() {
    if (!sessionId) return
    setUploadBusy(true)
    try {
      const r = await api.findTextbook(sessionId)
      if (r.sources) setSource((s) => ({ ...s, sources: r.sources, status: r.status, note: r.note }))
      await resync()
    } catch (e) {
      push('error', String(e.message || e))
    } finally {
      setUploadBusy(false)
    }
  }

  async function handleSelectTopic(node) {
    if (!sessionId) return
    if (isPreparingTopic.current) {
      push('system', 'Уже готовимся по предыдущей теме — дождитесь завершения.')
      return
    }
    setChatBusy(true)
    isPreparingTopic.current = true  // busy до финального WS-события (fire-and-forget)
    // Клик по теме суперсидирует ожидание ответа на предыдущий вопрос:
    // сбрасываем флаг, чтобы событие `system`/`source.progress` не сняли busy
    // во время подготовки (fix #2).
    isWaitingForAnswer.current = false
    // НЕ обнуляем current - показываем что готовимся по теме
    // node.title может быть URL — используем readable version
    const displayTitle = node.title && node.title.startsWith('http')
      ? (node.title.match(/https?:\/\/[^\/\s]+/)?.[0]?.replace('https://', '').replace('http://', '') || 'Источник')
      : (node.title || 'тема')
    push('system', `Готовимся по теме: ${displayTitle}...`)
    setProgressPhase({ stage: 'topic', message: `Готовимся по теме: ${displayTitle}...`, status: 'starting' })
    try {
      const r = await api.selectTopic(sessionId, node.id)
      setGraph((g) => ({ ...g, activeTopic: r.active_topic }))
      // Вопрос/урок придут через WS (source.progress → token → quiz.card / tutor.lesson) —
      // не ждём их в HTTP-ответе (оптимизация #2).
      resetBusyAfterTimeout()  // страховка, если WS-события вдруг не придут
    } catch (e) {
      push('error', String(e.message || e))
      isPreparingTopic.current = false
      setProgressPhase(null)
      setChatBusy(false)
    }
    // chatBusy сбросится при получении финального WS-события
  }

  function handleNewSession() {
    if (sessionIdRef.current) api.deleteSession(sessionIdRef.current)
    // Сброс всех UI-статусов и зависимых состояний перед новой сессией
    setFeed([])
    setCurrent(null)
    setSource({ status: null, note: null, sources: [], author: null, textbookUrl: '' })
    setGraph({ nodes: [], edges: [], activeTopic: null })
    setKnowledge({})
    setScore({ correct: 0, total: 0 })
    setQuizCount(0)
    setQuestionNum(0)
    setConfirmedOption(null)
    setAnswer('')
    setChatBusy(false)
    setUploadBusy(false)
    setProgressPhase(null)
    isWaitingForAnswer.current = false
    isPreparingTopic.current = false
    pendingAnswer.current = null
    currentKindAtSubmit.current = null
    if (answerTimeoutRef.current) clearTimeout(answerTimeoutRef.current)
    window.location.reload()
  }

  function handleConfirmOption() {
    if (confirmedOption) {
      submitAnswer()
    }
  }

  function handleCancelOption() {
    setConfirmedOption(null)
    setAnswer('')
  }

  function toggleQuickAnswer() {
    setQuickAnswer((v) => !v)
  }

  // «Включить любые источники» из предложения: сохраняем политику и повторяем поиск
  async function enableAnySources() {
    if (!student.student_id || !sessionId) return
    try {
      const updated = await api.putSourcePolicy(student.student_id, { allow_any_sources: true })
      setSourcePolicy(updated)
      setSourceProposal(null)
      await handleFind()
    } catch (e) {
      push('error', String(e.message || e))
    }
  }

  function openSourcesPanel() {
    setSourceProposal(null)
    setSourcePanelSignal((k) => k + 1)
  }

  // Ресайз боковой панели: pointer-drag по ручке, сохраняем ширину в localStorage
  useEffect(() => {
    const el = sidebarDragRef.current
    if (!el) return
    let dragging = false
    const onMove = (e) => {
      if (!dragging) return
      const w = Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, e.clientX))
      setSidebarWidth(w)
    }
    const onUp = () => {
      if (!dragging) return
      dragging = false
      document.body.classList.remove('resizing')
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      localStorage.setItem(SIDEBAR_KEY, String(el.dataset.w))
    }
    const onDown = (e) => {
      e.preventDefault()
      dragging = true
      document.body.classList.add('resizing')
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
    }
    el.addEventListener('pointerdown', onDown)
    return () => {
      el.removeEventListener('pointerdown', onDown)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
    }
  }, [])

  useEffect(() => {
    if (sidebarDragRef.current) sidebarDragRef.current.dataset.w = String(sidebarWidth)
  }, [sidebarWidth])

  return (
    <div className="app">
      <aside className="sidebar" style={{ width: sidebarWidth, minWidth: sidebarWidth }}>
        <div className="brand-row">
          <h1 className="brand">
            EduTutor
            <small>умный репетитор</small>
          </h1>
          <div className="brand-actions">
            <button className="btn small" onClick={handleNewSession} title="Создать новую сессию">
              Новая сессия
            </button>
            <div className="settings-gear" ref={settingsBtnRef}>
              <button className="gear-btn" onClick={() => setSettingsOpen(!settingsOpen)} title="Настройки">⚙</button>
              {settingsOpen && (
                <div className="settings-popup">
                  <button className="settings-close" onClick={() => setSettingsOpen(false)}>✕</button>
                  <div className="settings-header">Настройки</div>
                  <label className="settings-toggle">
                    <input type="checkbox" checked={quickAnswer} onChange={toggleQuickAnswer} />
                    <span className="toggle-track"><span className="toggle-thumb" /></span>
                    <span className="toggle-label">
                      Быстрый ответ
                      <small>{quickAnswer ? 'Автоотправка вариантов' : 'Подтверждение выбора'}</small>
                    </span>
                  </label>
                </div>
              )}
            </div>
          </div>
        </div>
        {sessionId && <div className="session-id">сессия: {sessionId}</div>}
        {/* intake.complete — истинен, когда карточка заполнена (статус из intakeStatus) */}
        <KnowledgeWikiPanel key={wikiReloadKey} studentId={student.student_id} studentName={student.student_name} intakeComplete={intake.complete} />
        <SessionHistoryPanel studentId={student.student_id} reloadKey={sessionHistoryReloadKey} />
        <SourceWhitelistPanel studentId={student.student_id} openSignal={sourcePanelSignal} onChanged={setSourcePolicy} />
        <KnowledgeGraphPanel
          nodes={graph.nodes}
          edges={graph.edges}
          activeTopic={graph.activeTopic}
          onSelect={handleSelectTopic}
          sessionId={sessionId}
        />
        <SourceSearchPanel status={source.status} note={source.note} sources={source.sources} author={source.author} onFind={handleFind} busy={uploadBusy} />
        <FileUpload onUpload={handleUpload} busy={uploadBusy} />
      </aside>
      <div className="sidebar-resizer" ref={sidebarDragRef} title="Изменить ширину панели" />
      <main className="chat">
        <ChatStream feed={feed} busy={chatBusy || uploadBusy} progressPhase={progressPhase} />
        {current &&
          (current.kind === 'quiz' ? (
            <QuizCard
              q={current}
              onSelect={onOption}
              questionNum={questionNum}
              totalQuestions={quizCount}
              selectedOption={confirmedOption}
              quickAnswer={quickAnswer}
            />
          ) : current.kind === 'intake_card' ? (
            <IntakeCard
              card={current.card}
              question={current.question}
              onSubmit={submitIntakeCard}
              disabled={chatBusy}
            />
          ) : (
            <IntakeWizard
              missing={current.missingFields ?? intake.missingFields}
              question={current.question}
              fieldValues={{
                learner_type: intake.learner_type,
                grade: intake.grade,
                subject: intake.subject,
                topic: intake.topic,
                has_textbook: intake.has_textbook,
                chapter: intake.chapter,
                mode: intake.mode,
              }}
              options={current.options || []}
              onAnswer={(v) => sendMessage(v)}
            />
          ))}
        {confirmedOption && (
          <div className="confirm-bar">
            Вы выбрали: <strong>{confirmedOption}</strong>
            <button className="btn-confirm" onClick={handleConfirmOption}>Подтвердить</button>
            <button className="btn-cancel" onClick={handleCancelOption}>Отмена</button>
          </div>
        )}
        {sourceProposal && (
          <div className="source-proposal">
            <div className="source-proposal__icon">🔎</div>
            <div className="source-proposal__text">{sourceProposal.message}</div>
            <div className="source-proposal__actions">
              <button className="btn small" onClick={enableAnySources}>Включить любые источники</button>
              <button className="btn small ghost" onClick={openSourcesPanel}>Изменить источники</button>
              <button className="btn small ghost" onClick={() => setSourceProposal(null)}>Позже</button>
            </div>
          </div>
        )}
        <div className="answerbar">
          <input
            ref={inputRef}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submitAnswer()}
            placeholder="Ваш ответ…"
            disabled={chatBusy || !sessionId}
          />
          <button onClick={submitAnswer} disabled={chatBusy || !answer.trim()}>
            {chatBusy ? '…' : 'Отправить'}
          </button>
        </div>
      </main>
    </div>
  )
}
