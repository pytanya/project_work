import { useCallback, useEffect, useRef, useState } from 'react'
import { api, wsUrl } from './api'
import ChatStream from './components/ChatStream'
import IntakeWizard from './components/IntakeWizard'
import QuizCard from './components/QuizCard'
import ProgressDashboard from './components/ProgressDashboard'
import SourceSearchPanel from './components/SourceSearchPanel'
import FileUpload from './components/FileUpload'
import './index.css'

function App() {
  const [sessionId, setSessionId] = useState(null)
  const [feed, setFeed] = useState([])
  const [current, setCurrent] = useState(null)
  const [intake, setIntake] = useState({ missingFields: [], complete: false })
  const [source, setSource] = useState({ status: null, note: null })
  const [knowledge, setKnowledge] = useState({})
  const [score, setScore] = useState({ correct: 0, total: 0 })
  const [answer, setAnswer] = useState('')
  const [busy, setBusy] = useState(false)
  const wsRef = useRef(null)

  const push = useCallback((kind, text, data) => {
    setFeed((f) => [...f, { id: `${Date.now()}-${Math.random()}`, kind, text, data }])
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
        case 'tutor.summary':
          setKnowledge(d.knowledge_map || {})
          setScore({ correct: d.correct || 0, total: d.total || 0 })
          push('summary', `Квиз завершён: правильных ${d.correct}/${d.total}`)
          break
        case 'source.progress':
          setSource({ status: d.status, note: d.message })
          push('source', d.message)
          break
        case 'source.failed':
          setSource({ status: 'failed', note: d.message })
          push('error', d.message)
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
    [push],
  )

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
      } catch (e) {
        push('error', `Не удалось создать сессию: ${e.message}`)
      }
    }
    init()
    return () => {
      cancelled = true
      if (wsRef.current) wsRef.current.close()
    }
  }, [handleEvent, push])

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
    try {
      const r = await api.uploadFile(sessionId, file)
      push('system', `Файл «${r.filename}» ${r.status === 'ready' ? 'проиндексирован' : 'принят'}`)
      if (r.status === 'ready') setSource({ status: 'ready', note: r.note })
    } catch (e) {
      push('error', String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  async function handleFind() {
    if (!sessionId) return
    setBusy(true)
    try {
      await api.findTextbook(sessionId)
    } catch (e) {
      push('error', String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <h1 className="brand">EduTutor</h1>
        <ProgressDashboard knowledge={knowledge} correct={score.correct} total={score.total} />
        <SourceSearchPanel status={source.status} note={source.note} onFind={handleFind} />
        <FileUpload onUpload={handleUpload} />
      </aside>
      <main className="chat">
        <ChatStream feed={feed} />
        {current &&
          (current.kind === 'quiz' ? (
            <QuizCard q={current} onSelect={onOption} />
          ) : (
            <IntakeWizard missing={intake.missingFields} question={current.question} />
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
