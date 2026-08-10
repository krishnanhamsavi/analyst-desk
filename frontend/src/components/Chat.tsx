import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

interface Message {
  role: 'user' | 'assistant'
  content: string
}

const FALLBACK_CHIPS = [
  'Explain this simply',
  'Why is this risky?',
  'What could go wrong?',
  'Is now a good time?',
]

/**
 * The follow-up conversation.
 *
 * Suggested chips matter more than they look: a blank box asks a non-expert to
 * know what to ask, which is exactly the knowledge they don't have. Tappable
 * questions turn a one-shot report into a conversation they can actually start.
 */
export function Chat({
  runId,
  suggestions,
  company,
}: {
  runId: string
  suggestions?: string[]
  company: string
}) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const scroller = useRef<HTMLDivElement>(null)
  const chips = suggestions?.length ? suggestions : FALLBACK_CHIPS

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  async function ask(question: string) {
    const text = question.trim()
    if (!text || streaming) return

    const history = messages.map((m) => ({ role: m.role, content: m.content }))
    setMessages((prev) => [...prev, { role: 'user', content: text }, { role: 'assistant', content: '' }])
    setInput('')
    setStreaming(true)

    try {
      await api.chat(runId, text, history, (chunk) => {
        setMessages((prev) => {
          const next = [...prev]
          next[next.length - 1] = {
            role: 'assistant',
            content: next[next.length - 1].content + chunk,
          }
          return next
        })
      })
    } catch (error) {
      setMessages((prev) => {
        const next = [...prev]
        next[next.length - 1] = {
          role: 'assistant',
          content: `Sorry — I couldn't answer that. ${(error as Error).message}`,
        }
        return next
      })
    } finally {
      setStreaming(false)
    }
  }

  return (
    <section className="rounded-xl border border-line bg-surface/60">
      <div className="border-b border-line-soft px-5 py-3">
        <h2 className="text-sm font-semibold">Ask the analyst</h2>
        <p className="text-xs text-faint">
          Follow-up questions about {company}, answered from the same sources.
        </p>
      </div>

      {messages.length > 0 && (
        <div ref={scroller} className="max-h-[26rem] space-y-3 overflow-y-auto px-5 py-4">
          {messages.map((message, i) => (
            <div
              key={i}
              className={`animate-in ${message.role === 'user' ? 'flex justify-end' : ''}`}
            >
              <div
                className={
                  message.role === 'user'
                    ? 'max-w-[85%] rounded-2xl rounded-br-sm bg-accent/15 px-3.5 py-2 text-sm text-text'
                    : 'max-w-full text-sm leading-relaxed whitespace-pre-wrap text-muted'
                }
              >
                {message.content ||
                  (streaming && i === messages.length - 1 ? (
                    <span className="inline-flex gap-1">
                      <span className="animate-working h-1.5 w-1.5 rounded-full bg-faint" />
                      <span className="animate-working h-1.5 w-1.5 rounded-full bg-faint [animation-delay:0.2s]" />
                      <span className="animate-working h-1.5 w-1.5 rounded-full bg-faint [animation-delay:0.4s]" />
                    </span>
                  ) : null)}
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="px-5 pt-3 pb-4">
        <div className="mb-2.5 flex flex-wrap gap-2">
          {chips.map((chip) => (
            <button
              key={chip}
              onClick={() => ask(chip)}
              disabled={streaming}
              className="rounded-full border border-line px-3 py-1.5 text-xs text-muted transition hover:border-accent/40 hover:text-text disabled:opacity-40"
            >
              {chip}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && ask(input)}
            placeholder="Ask anything about this analysis…"
            disabled={streaming}
            className="flex-1 rounded-xl border border-line bg-ink px-4 py-2.5 text-sm outline-none transition placeholder:text-faint focus:border-accent/60 disabled:opacity-50"
          />
          <button
            onClick={() => ask(input)}
            disabled={streaming || !input.trim()}
            className="rounded-xl bg-accent px-4 py-2.5 text-sm font-medium text-ink transition hover:brightness-110 disabled:bg-line disabled:text-faint"
          >
            Ask
          </button>
        </div>
      </div>
    </section>
  )
}
