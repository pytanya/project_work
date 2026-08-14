import { useCallback, useEffect, useRef, useState } from 'react'
import { api, wsUrl } from './api'
import ChatStream from './components/ChatStream'
import IntakeWizard from './components/IntakeWizard'
import QuizCard from './components/QuizCard'
import ProgressDashboard from './components/ProgressDashboard'
import SourceSearchPanel from './components/SourceSearchPanel'
import FileUpload from './components/FileUpload'
import KnowledgeGraphPanel from './components/KnowledgeGraphPanel'
import './index.css'

function App() {
  const [sessionId, setSessionId] = useState(null)
  const [feed, setFeed] = useState([])
  const [current, setCurrent] = useState(null)
  const [intake, setIntake] = useState({ missingFields: [], complete: false })
  const [source, setSource] = useState({ status: null, note: null })
  const [graph, setGraph] = useState({ nodes: [], edges: [], activeTopic: null })
  const [knowledge, setKnowledge] = useState({})
  const [score, setScore] = useState({ correct: 0, total: 0 })
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const wsRef = useRef(null)
  const sessionIdRef = useRef(null)

  const push = useCallback((kind, text, data) => {
    setFeed((f) => {
      // Дедуп: первое сообщение приходит дважды (HTTP + WS-реплей) — пропускаем
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
          push('explanation', d.message, d)
          break
        case 'tutor.lesson':
          push('lesson', d.text, { topic: d.topic })
          break
        case 'tutor.summary':
          setKnowledge(d.knowledge_map || {})
          setScore({ correct: d.correct || 0, total: d.total || 0 })
          push('summary', `Квиз завершён: правильных ${d.correct}/${d.total}`)
          break
        case 'source.progress':
          setSource({ status: d.status, note: d.message })
          setCurrent(null) // поиск/OCR идёт — устаревший вопрос прячем
          push('source', d.message)
          break
        case 'source.failed':
          setSource({ status: 'failed', note: d.message })
          setCurrent(null)
          push('error', d.message)
          break
        case 'graph.ready':
          refreshGraph()
          push('system', `Построен граф знаний: ${d.nodes} тем`)
          break
        case 'system':
          push('system', d.message)
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
    async function init() {
      try {
        const { session_id } = await api.createSession()
        if (cancelled) return
        setSessionId(session_id)
        const ws = new WebSocket(wsUrl(session_id))
        ws.onmessage = (e) => {
          try {
            handleEvent(JSON.parse(e.data))
          } catch (_) {}
        }
        wsRef.current = ws
        const st = await api.intakeStatus(session_id)
        setIntake({ missingFields: st.missing_fields, complete: st.complete })
        if (!st.complete && st.next_question) {
          setCurrent({ kind: 'intake', question: st.next_question, missingFields: st.missing_fields })
          push('intake', st.next_question)
        }
        refreshGraph()
      } catch (e) {
        push('error', `Не удалось создать сессию: ${e.message}`)
      }
    }
    init()
    // Удаляем сессию на бэкенде при закрытии вкладки (не копим мусор)
    const unload = () => {
      if (sessionIdRef.current) api.deleteSession(sessionIdRef.current)
    }
    window.addEventListener('beforeunload', unload)
    return () => {
      cancelled = true
      window.removeEventListener('beforeunload', unload)
      if (wsRef.current) wsRef.current.close()
    }
  }, [handleEvent, push])

  // resync: подтягиваем актуальное состояние сессии по HTTP (страховка, если WS
  // отвалился во время долгой обработки — парсинг/индексация > WS-idle)
  const resync = useCallback(async () => {
    if (!sessionIdRef.current) return
    try {
      const d = await api.getSession(sessionIdRef.current)
      if (d.current_question) {
        const q = d.current_question
        setCurrent({
          kind: 'quiz', question: q.question, options: q.options, answerType: q.answer_type,
          topic: q.topic, difficulty: q.difficulty, questionId: q.question_id,
        })
      } else if (d.awaiting_topic && d.agent_question) {
        setCurrent({ kind: 'intake', question: d.agent_question, missingFields: ['topic'] })
      } else if (d.intake_field && d.agent_question) {
        setCurrent({ kind: 'intake', question: d.agent_question, missingFields: d.missing_fields })
      } else {
        setCurrent(null)
      }
      setKnowledge(d.knowledge_map || {})
      setScore({ correct: d.correct_count || 0, total: d.answered_count || 0 })
      if (d.source_status) setSource({ status: d.source_status, note: d.source_note })
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
    push('user', text)
    setBusy(true)
    try {
      if (current?.kind === 'intake') {
        await api.postIntake(sessionId, text)
      } else {
        await api.postMessage(sessionId, text)
      }
      const st = await api.intakeStatus(sessionId)
      setIntake({ missingFields: st.missing_fields, complete: st.complete })
      if (st.complete) setCurrent((c) => (c?.kind === 'intake' ? null : c))
      await resync()
    } catch (e) {
      push('error', String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const onOption = (opt) => {
    setAnswer(opt)
  }

  async function handleUpload(file) {
    if (!sessionId) return
    setBusy(true)
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
      setBusy(false)
    }
  }

  async function handleFind() {
    if (!sessionId) return
    setBusy(true)
    try {
      await api.findTextbook(sessionId)
      await resync()
    } catch (e) {
      push('error', String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function handleSelectTopic(node) {
    if (!sessionId) return
    setBusy(true)
    setCurrent(null)
    push('system', `Готовимся по теме: ${node.title}…`)
    try {
      const r = await api.selectTopic(sessionId, node.id)
      setGraph((g) => ({ ...g, activeTopic: r.active_topic }))
      await resync()
    } catch (e) {
      push('error', String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  function handleNewSession() {
    // закрываем текущую и перезагружаем страницу (создаст свежую сессию)
    if (sessionIdRef.current) api.deleteSession(sessionIdRef.current)
    window.location.reload()
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand-row">
          <h1 className="brand">
            EduTutor
            <small>умный репетитор</small>
          </h1>
          <button className="btn small" onClick={handleNewSession} title="Создать новую сессию">
            Новая сессия
          </button>
        </div>
        {sessionId && <div className="session-id">сессия: {sessionId}</div>}
        <ProgressDashboard knowledge={knowledge} correct={score.correct} total={score.total} />
        <SourceSearchPanel status={source.status} note={source.note} onFind={handleFind} busy={busy} />
        <KnowledgeGraphPanel
          nodes={graph.nodes}
          activeTopic={graph.activeTopic}
          onSelect={handleSelectTopic}
          busy={busy}
        />
        <FileUpload onUpload={handleUpload} busy={busy} />
      </aside>
      <main className="chat">
        <ChatStream feed={feed} />
        {current &&
          (current.kind === 'quiz' ? (
            <QuizCard q={current} onSelect={onOption} />
          ) : (
            <IntakeWizard missing={current.missingFields ?? intake.missingFields} question={current.question} />
          ))}
        <div className="answerbar">
          <input
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && submitAnswer()}
            placeholder="Ваш ответ…"
            disabled={busy || !sessionId}
          />
          <button onClick={submitAnswer} disabled={busy || !answer.trim()}>
            {busy ? '…' : 'Отправить'}
          </button>
        </div>
      </main>
    </div>
  )
}

export default App
