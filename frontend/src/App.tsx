import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { Chat } from './components/Chat'
import { Memo } from './components/Memo'
import { SearchScreen } from './components/SearchScreen'
import { TradingFloor } from './components/TradingFloor'
import type { DeskEvent, HistoryRow, RunPayload } from './types'

type View = 'search' | 'running' | 'memo'

export default function App() {
  const [view, setView] = useState<View>('search')
  const [runId, setRunId] = useState<string | null>(null)
  const [events, setEvents] = useState<DeskEvent[]>([])
  const [run, setRun] = useState<RunPayload | null>(null)
  const [status, setStatus] = useState('idle')
  const [error, setError] = useState<string | null>(null)
  const [history, setHistory] = useState<HistoryRow[]>([])
  const [beginner, setBeginner] = useState(true)
  const [pending, setPending] = useState<{ company: string; ticker: string | null }>({
    company: '',
    ticker: null,
  })
  const unsubscribe = useRef<(() => void) | null>(null)

  const loadHistory = useCallback(async () => {
    try {
      setHistory((await api.history(20)).runs)
    } catch {
      /* history is a nicety; never block the app on it */
    }
  }, [])

  useEffect(() => {
    loadHistory()
    return () => unsubscribe.current?.()
  }, [loadHistory])

  // The company name arrives in the resolve event, so the header can show a real
  // name within a second rather than echoing whatever the user typed.
  useEffect(() => {
    const resolved = events.find((e) => e.type === 'run_stage' && e.data?.resolved)
    if (resolved) {
      setPending({
        company: resolved.data.name ?? resolved.data.resolved,
        ticker: resolved.data.resolved,
      })
    }
  }, [events])

  async function startRun(query: string, horizon: string, userView: string | null) {
    setError(null)
    setEvents([])
    setRun(null)
    setPending({ company: query, ticker: null })
    setStatus('running')
    setView('running')

    try {
      const started = await api.startRun(query, horizon, userView)
      setRunId(started.run_id)

      unsubscribe.current = api.streamRun(
        started.run_id,
        (event) => setEvents((prev) => [...prev, event]),
        async (finalStatus) => {
          setStatus(finalStatus)
          try {
            const payload = await api.getRun(started.run_id)
            setRun(payload)
            if (payload.memo) setView('memo')
            else setError(payload.error ?? 'The run finished without producing a memo.')
          } catch (err) {
            setError((err as Error).message)
          }
          loadHistory()
        },
      )
    } catch (err) {
      setError((err as Error).message)
      setStatus('failed')
      setView('search')
    }
  }

  async function openRun(id: string) {
    setError(null)
    try {
      const payload = await api.getRun(id)
      setRun(payload)
      setRunId(id)
      setEvents([])
      setPending({ company: payload.company_name ?? '', ticker: payload.ticker })
      setStatus(payload.status ?? 'done')
      setView(payload.memo ? 'memo' : 'search')
      if (!payload.memo) setError('That run has no memo to show.')
    } catch (err) {
      setError((err as Error).message)
    }
  }

  function reset() {
    unsubscribe.current?.()
    setView('search')
    setRun(null)
    setEvents([])
    setRunId(null)
    setStatus('idle')
    setError(null)
    loadHistory()
  }

  return (
    <div className="relative min-h-full">
      {view !== 'search' && (
        <nav className="sticky top-0 z-40 border-b border-line bg-base/85 backdrop-blur">
          <div className="mx-auto flex max-w-7xl items-center justify-between gap-4 px-4 py-3">
            <button
              onClick={reset}
              className="flex items-center gap-2 text-sm font-semibold transition hover:text-accent"
            >
              <span className="h-1.5 w-1.5 rounded-full bg-bull" />
              Analyst Desk
            </button>

            <div className="flex items-center gap-3">
              {view === 'memo' && (
                <div className="flex items-center rounded-lg border border-line p-0.5 text-xs">
                  <button
                    onClick={() => setBeginner(true)}
                    className={`rounded px-2.5 py-1 transition ${
                      beginner ? 'bg-accent/15 text-accent' : 'text-muted hover:text-text'
                    }`}
                  >
                    Beginner
                  </button>
                  <button
                    onClick={() => setBeginner(false)}
                    className={`rounded px-2.5 py-1 transition ${
                      !beginner ? 'bg-accent/15 text-accent' : 'text-muted hover:text-text'
                    }`}
                  >
                    Analyst
                  </button>
                </div>
              )}
              <button
                onClick={reset}
                className="rounded-lg border border-line px-3 py-1.5 text-xs text-muted transition hover:border-accent/40 hover:text-text"
              >
                New analysis
              </button>
            </div>
          </div>
        </nav>
      )}

      {error && (
        <div className="mx-auto max-w-3xl px-4 pt-4">
          <div className="rounded-xl border border-bear/40 bg-bear/10 px-4 py-3 text-sm text-bear">
            {error}
          </div>
        </div>
      )}

      {view === 'search' && (
        <SearchScreen
          onStart={startRun}
          history={history}
          onOpenRun={openRun}
          busy={status === 'running'}
        />
      )}

      {view === 'running' && (
        <TradingFloor
          events={events}
          company={pending.company}
          ticker={pending.ticker}
          status={status}
        />
      )}

      {view === 'memo' && run && (
        <div className="mx-auto max-w-4xl space-y-4 px-4 py-6">
          <header className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h1 className="text-2xl font-semibold">
                {run.company_name}{' '}
                {run.ticker && <span className="font-mono text-lg text-accent">{run.ticker}</span>}
              </h1>
              <p className="text-xs text-muted">
                {run.profile?.sector}
                {run.profile?.industry ? ` · ${run.profile.industry}` : ''}
                {run.elapsed_s ? ` · analysed in ${Math.round(run.elapsed_s)}s` : ''}
              </p>
            </div>
            {events.length > 0 && (
              <button
                onClick={() => setView('running')}
                className="text-xs text-muted underline transition hover:text-accent"
              >
                Replay how the desk got here
              </button>
            )}
          </header>

          <Memo run={run} beginner={beginner} />

          {runId && (
            <Chat
              runId={runId}
              suggestions={run.suggested_questions}
              company={run.company_name ?? run.ticker ?? 'this company'}
            />
          )}
        </div>
      )}
    </div>
  )
}
