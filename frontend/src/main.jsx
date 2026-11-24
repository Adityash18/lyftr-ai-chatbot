
import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './style.css'

const API = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
const newSession = () => `s-${crypto.randomUUID()}`

function App() {
  const [sessionId, setSessionId] = useState(newSession())
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [showPrompt, setShowPrompt] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const bottom = useRef(null)

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  async function send() {
    const message = input.trim()
    if (!message || busy) return

    setInput('')
    setError('')
    setMessages(prev => [...prev, { role: 'user', content: message }])
    setBusy(true)

    try {
      const response = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          message,
          ...(systemPrompt.trim()
            ? { system_prompt: systemPrompt.trim() }
            : {})
        })
      })

      const data = await response.json().catch(() => ({}))

      if (!response.ok) {
        throw new Error(data.detail || `Request failed (${response.status})`)
      }

      setMessages(prev => [
        ...prev,
        {
          role: 'assistant',
          content: data.reply,
          metadata: data.metadata
        }
      ])
    } catch (e) {
      const msg = e.message || 'Unable to reach the server.'
      setError(msg)
      setMessages(prev => [...prev, { role: 'error', content: msg }])
    } finally {
      setBusy(false)
    }
  }

  async function loadHistory() {
    try {
      const r = await fetch(`${API}/sessions/${sessionId}/messages`)

      if (!r.ok) throw new Error('History unavailable')

      const data = await r.json()
      setMessages(data.messages || [])
      setError('')
    } catch {
      setError('No saved history found for this session yet.')
    }
  }

  function startNew() {
    setSessionId(newSession())
    setMessages([])
    setError('')
    setInput('')
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">✦</div>
          <div>
            <strong>lyftr<span>.ai</span></strong>
            <small>Intelligent conversations</small>
          </div>
        </div>

        <div className="header-actions">
          <button className="ghost" onClick={loadHistory}>
            ↻ Load history
          </button>
          <button className="new-chat" onClick={startNew}>
            ＋ New chat
          </button>
        </div>
      </header>

      <section className="workspace">
        <div className="intro">
          <div className="eyebrow">
            <span className="pulse"></span> AI ASSISTANT
          </div>
          <h1>
            What can I help<br />
            <em>you with today?</em>
          </h1>
          <p>
            Ask anything. Your conversation stays in context,
            with resilient AI routing behind every response.
          </p>
        </div>

        {messages.length === 0 && (
          <div className="suggestions">
            {[
              'Explain how an API works',
              'Give me a 7-day learning plan',
              'What is retrieval augmented generation?'
            ].map((s, i) => (
              <button key={s} onClick={() => setInput(s)}>
                <span>{['✧', '⌁', '◈'][i]}</span>
                {s}
                <b>↗</b>
              </button>
            ))}
          </div>
        )}

        <div className="conversation">
          {messages.map((m, i) => (
            <div key={i} className={`message-row ${m.role}`}>
              {m.role === 'assistant' && (
                <div className="avatar">✦</div>
              )}

              <div className="message-content">
                <div className="bubble">
                  {m.role === 'assistant' ? (
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {m.content}
                    </ReactMarkdown>
                  ) : (
                    m.content
                  )}
                </div>

                {m.role === 'assistant' && m.metadata && (
                  <div className="metadata">
                    <span className={`source ${
                      m.metadata.resolution_source === 'LLM_CACHE'
                        ? 'cached'
                        : ''
                    }`}>
                      {m.metadata.resolution_source}
                    </span>

                    <span>{m.metadata.model}</span>
                    <span>{m.metadata.latency_ms} ms</span>
                    <span>↑ {m.metadata.input_tokens} in</span>
                    <span>↓ {m.metadata.output_tokens} out</span>

                    {m.metadata.key_id && (
                      <span>{m.metadata.key_id}</span>
                    )}

                    {m.metadata.fallback_attempts > 0 && (
                      <span>
                        ↪ {m.metadata.fallback_attempts} fallback(s)
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}

          {busy && (
            <div className="message-row assistant">
              <div className="avatar">✦</div>
              <div className="bubble typing">
                <i></i><i></i><i></i>
              </div>
            </div>
          )}

          <div ref={bottom} />
        </div>

        {error && <div className="error-note">⚠ {error}</div>}

        <div className="composer-wrap">
          {showPrompt && (
            <textarea
              data-testid="system-prompt-input"
              className="system-prompt"
              value={systemPrompt}
              onChange={e => setSystemPrompt(e.target.value)}
              placeholder="Set a system prompt for this session (optional)"
              rows="2"
            />
          )}

          <div className="composer">
            <textarea
              data-testid="chat-input"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  send()
                }
              }}
              placeholder="Message Lyftr AI..."
              rows="1"
            />

            <div className="composer-tools">
              <button
                className="prompt-toggle"
                onClick={() => setShowPrompt(v => !v)}
                title="System prompt"
              >
                ⚙ <span>System prompt</span>
              </button>

              <span className="enter-hint">↵ Enter to send</span>

              <button
                data-testid="send-button"
                className="send"
                onClick={send}
                disabled={busy || !input.trim()}
              >
                ↑
              </button>
            </div>
          </div>

          <p className="disclaimer">
            AI can make mistakes. Check important information.
          </p>
        </div>
      </section>
    </main>
  )
}

createRoot(document.getElementById('root')).render(<App />)
