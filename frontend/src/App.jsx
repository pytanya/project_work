import { useCallback, useEffect, useRef, useState } from 'react'
import { api, wsUrl } from './api'
import ChatStream from './components/ChatStream'
import IntakeWizard from './components/IntakeWizard'
import QuizCard from './components/QuizCard'
import ProgressDashboard from './components/ProgressDashboard'
import SourceSearchPanel from './components/SourceSearchPanel'
import FileUpload from './components/FileUpload'
import KnowledgeGraphPanel from './components/KnowledgeGraphPanel'
import KnowledgeWikiPanel from './components/KnowledgeWikiPanel'
import './index.css'

const STORAGE_KEY = 'edututor_settings'

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

export default function App() {
  const [sessionId, setSessionId] = useState(null)
  const [feed, setFeed] = useState([])
  const [current, setCurrent] = useState(null)
  const [intake, setIntake] = useState({ missingFields: [], complete: false })
  const [source, setSource] = useState({ status: null, note: null, sources: [], author: null, textbookUrl: '' })
  const [graph, setGraph] = useState({ nodes: [], edges: [], activeTopic: null })
  const [knowledge, setKnowledge] = useState({})
  const [wikiReloadKey, setWikiReloadKey] = useState(0)
  const [score, setScore] = useState({ correct: 0, total: 0 })
  const [quizCount, setQuizCount] = useState(0)
  const [answer, setAnswer] = useState('')
  const [confirmedOption, setConfirmedOption] = useState(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [quickAnswer, setQuickAnswer] = useState(() => loadSettings().quickAnswer !== false)
  // Разделение busy: upload для индексации чат для квиза/сообщений
  const [uploadBusy, setUploadBusy] = useState(false)
  const [chatBusy, setChatBusy] = useState(false)
  // Счётчик вопросов квиза из записей
  const [questionNum, setQuestionNum] = useState(0)
  const wsRef = useRef(null)
  const sessionIdRef = useRef(null)
  const inputRef = useRef(null)
  const settingsBtnRef = useRef(null)
  // Refs: отслеживаем ожидаем ли результат ответа на текущий вопрос
  const pendingAnswer = useRef(null)       // текст отправленного ответа
  const currentKindAtSubmit = useRef(null) // kind экрана в момент отправки
  const isWaitingForAnswer = useRef(false) // флаг: ждём WS событие после отправки

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
      const last = f[f.length - 1]
      if (last && last.kind === kind && last.text === text) return f
      return [...f, { id: `${Date.now()}-${Math.random()}`, kind, text, data }]
    })
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
      // и получили событие от бэкенда (quiz.card, tutor.explanation, system, etc.)
      if (isWaitingForAnswer.current) {
        const answerResolvedEvents = [
          'quiz.card', 'tutor.explanation', 'system', 
          'tutor.summary', 'intake.question', 'source.progress'
        ]
        if (answerResolvedEvents.includes(evt.event)) {
          setChatBusy(false)
          isWaitingForAnswer.current = false
        }
      }
      
      switch (evt.event) {
        case 'intake.question':
          setCurrent({ kind: 'intake', question: d.question, missingFields: d.missing_fields })
          push('intake', d.question)
          break
        case 'quiz.card':
          setCurrent({
            kind: 'quiz',
            question: d.question,
            options: d.options,
            answerType: d.answer_type,
            topic: d.topic,
            difficulty: d.difficulty,
            questionId: d.question_id,
          })
          push('quiz', d.question)
          break
        case 'tutor.explanation':
          setCurrent(null)
          // Логирование для отладки LaTeX
          console.log('tutor.explanation message:', JSON.stringify(d.message?.substring(0, 200)))
          push('explanation', d.message, d)
          break
        case 'tutor.lesson':
          push('lesson', d.text, { topic: d.topic })
          break
        case 'tutor.summary':
          setCurrent(null)
          setKnowledge(d.knowledge_map || {})
          setScore({ correct: d.correct || 0, total: d.total || 0 })
          setQuizCount(d.total || 0)
          setQuestionNum(d.total || 0)
          push('summary', `Квиз завершён: правильных ${d.correct}/${d.total}`)
          break
        case 'source.progress':
          setSource({ status: d.status, note: d.message })
          // Исправление #1: правильная проверка active question
          // Проверяем что current имеет valid kind ('quiz' или 'intake')
          // Вместо некорректной проверки несуществующих полей current.current_question / current.agent_question
          if (!current || (current.kind !== 'quiz' && current.kind !== 'intake')) {
            setCurrent(null)
          }
          push('source', d.message)
          break
        case 'source.failed':
          setSource({ status: 'failed', note: d.message })
          // Исправление #1 (duplicate): та же корректная проверка для source.failed
          if (!current || (current.kind !== 'quiz' && current.kind !== 'intake')) {
            setCurrent(null)
          }
          push('error', d.message)
          break
        case 'graph.ready':
          refreshGraph()
          push('system', `Построен граф знаний: ${d.nodes} тем`)
          break
        case 'wiki.updated':
          setWikiReloadKey((k) => k + 1)
          break
        case 'system':
          setCurrent(null)
          push('system', d.message)
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
    [push, refreshGraph],
  )

  useEffect(() => {
    sessionIdRef.current = sessionId
  }, [sessionId])

  useEffect(() => {
    let cancelled = false
    let reconnectAttempts = 0
    let reconnectTimer = null

    const connectWs = (sid) => {
      const ws = new WebSocket(wsUrl(sid))
      ws.onmessage = (e) => {
        try {
          handleEvent(JSON.parse(e.data))
        } catch (_) {}
      }
      // auto-reconnect: бэкенд мог перезапуститься (WS закрылся) — переподключаемся
      ws.onclose = () => {
        if (cancelled || !sessionIdRef.current) return
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
            const r = await api.createSession()
            sessionId = r.session_id
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
        if (!st.complete && st.next_question) {
          setCurrent({ kind: 'intake', question: st.next_question, missingFields: st.missing_fields })
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
      // Если фронтенд уже показывает активный вопрос (quiz/intake), не перезаписываем current
      const hasFrontendActiveQuestion = current && (current.kind === 'quiz' || current.kind === 'intake')
      
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
      } else if (d.agent_question && !hasFrontendActiveQuestion) {
        setCurrent({ kind: 'intake', question: d.agent_question, missingFields: d.missing_fields || [] })
      }
      // else: оставляем текущий current без изменений если фронтенд уже показывает вопрос
      
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
  }, [push])

  async function submitAnswer() {
    const text = answer.trim()
    if (!text || !sessionId) return
    setAnswer('')
    setConfirmedOption(null)
    push('user', text)
    setChatBusy(true)
    isWaitingForAnswer.current = true  // помечаем что ждём WS событие от бэкенда
    
    // Таймаут fallback: если WS событие не пришло за 15 секунд — сбрасываем busy
    const timeout = setTimeout(() => {
      if (isWaitingForAnswer.current) {
        setChatBusy(false)
        isWaitingForAnswer.current = false
      }
    }, 15000)
    
    try {
      if (current?.kind === 'intake') {
        await api.postIntake(sessionId, text)
      } else {
        await api.postMessage(sessionId, text)
      }
      // resync убран: WS события уже обновляют UI (quiz.card, tutor.explanation, system)
      // Но оставляем intakeStatus для корректной работы intake чек-листа
      const st = await api.intakeStatus(sessionId)
      setIntake({ missingFields: st.missing_fields, complete: st.complete })
    } catch (e) {
      push('error', String(e.message || e))
      setChatBusy(false)
      isWaitingForAnswer.current = false
    } finally {
      clearTimeout(timeout)
      // busy сбросится при получении WS события или по timeout
    }
  }

  const onOption = (opt) => {
    setAnswer(opt)
    if (quickAnswer) {
      setConfirmedOption(null)
      submitAnswer()
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
    setChatBusy(true)
    // НЕ обнуляем current - показываем что готовимся по теме
    // node.title может быть URL — используем readable version
    const displayTitle = node.title && node.title.startsWith('http')
      ? (node.title.match(/https?:\/\/[^\/\s]+/)?.[0]?.replace('https://', '').replace('http://', '') || 'Источник')
      : (node.title || 'тема')
    push('system', `Готовимся по теме: ${displayTitle}...`)
    try {
      const r = await api.selectTopic(sessionId, node.id)
      setGraph((g) => ({ ...g, activeTopic: r.active_topic }))
      // Если бэкенд уже сгенерировал вопрос - обновляем UI
      if (r.question) {
        const q = r.question
        setCurrent({
          kind: 'quiz', question: q.question, options: q.options, answerType: q.answer_type,
          topic: q.topic, difficulty: q.difficulty, questionId: q.question_id,
        })
      } else if (r.next_question) {
        // Есть следующий вопрос (intake)
        setCurrent({ kind: 'intake', question: r.next_question, missingFields: [] })
      }
      // resync не нужен - уже получили данные из selectTopic
    } catch (e) {
      push('error', String(e.message || e))
    } finally {
      setChatBusy(false)
    }
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
    isWaitingForAnswer.current = false
    pendingAnswer.current = null
    currentKindAtSubmit.current = null
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

  return (
    <div className="app">
      <aside className="sidebar">
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
        <ProgressDashboard knowledge={knowledge} correct={score.correct} total={score.total} />
        <SourceSearchPanel status={source.status} note={source.note} sources={source.sources} author={source.author} onFind={handleFind} busy={uploadBusy} />
        <KnowledgeGraphPanel
          nodes={graph.nodes}
          edges={graph.edges}
          activeTopic={graph.activeTopic}
          onSelect={handleSelectTopic}
          busy={chatBusy}
        />
        <KnowledgeWikiPanel key={wikiReloadKey} />
        <FileUpload onUpload={handleUpload} busy={uploadBusy} />
      </aside>
      <main className="chat">
        <ChatStream feed={feed} busy={chatBusy || uploadBusy} />
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
          ) : (
            <IntakeWizard missing={current.missingFields ?? intake.missingFields} question={current.question} />
          ))}
        {confirmedOption && (
          <div className="confirm-bar">
            Вы выбрали: <strong>{confirmedOption}</strong>
            <button className="btn-confirm" onClick={handleConfirmOption}>Подтвердить</button>
            <button className="btn-cancel" onClick={handleCancelOption}>Отмена</button>
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
