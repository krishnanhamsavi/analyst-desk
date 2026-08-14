import { useEffect, useState } from 'react'
import { api } from '../api'
import type { HistoryRow, Resolution } from '../types'

const EXAMPLES = ['Apple', 'Tesla', 'Nvidia', 'Coca-Cola', 'Microsoft']

const HORIZONS = [
  {
    value: 'short',
    label: 'Right now',
    sub: 'next few weeks',
    hint: 'Weighs momentum, recent price action and live news. The "should I look at this today?" lens.',
  },
  {
    value: 'medium',
    label: '6-12 months',
    sub: 'the default',
    hint: 'Balances the business fundamentals against current momentum.',
  },
  {
    value: 'long',
    label: '3-5 years',
    sub: 'long term',
    hint: 'Business quality and competitive position dominate. Short-term noise is ignored.',
  },
]

interface Props {
  onStart: (query: string, horizon: string, userView: string | null) => void
  history: HistoryRow[]
  onOpenRun: (runId: string) => void
  busy: boolean
}

export function SearchScreen({ onStart, history, onOpenRun, busy }: Props) {
  const [query, setQuery] = useState('')
  const [horizon, setHorizon] = useState('medium')
  const [view, setView] = useState('')
  const [showView, setShowView] = useState(false)
  const [resolution, setResolution] = useState<Resolution | null>(null)
  const [checking, setChecking] = useState(false)

  // Resolve as they type so "Did you mean Apple Inc. (AAPL)?" appears before
  // they commit several minutes of analysis to the wrong company.
  useEffect(() => {
    const text = query.trim()
    if (text.length < 2) {
      setResolution(null)
      return
    }
    const timer = setTimeout(async () => {
      setChecking(true)
      try {
        setResolution(await api.resolve(text))
      } catch {
        setResolution(null)
      } finally {
        setChecking(false)
      }
    }, 450)
    return () => clearTimeout(timer)
  }, [query])

  const canRun = query.trim().length > 0 && !busy

  function start(overrideTicker?: string) {
    if (!canRun && !overrideTicker) return
    onStart(overrideTicker ?? query.trim(), horizon, view.trim() || null)
  }

  return (
    <div className="relative mx-auto flex min-h-full max-w-4xl flex-col justify-center px-6 py-16">
      <header className="mb-8 text-center">
        <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 text-[11px] tracking-widest text-muted uppercase">
          <span className="h-1.5 w-1.5 rounded-full bg-bull" />
          Multi-agent equity research
        </div>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">Analyst Desk</h1>
        <p className="mx-auto mt-3 max-w-2xl text-[15px] leading-relaxed text-muted">
          Every article about a stock is either hype or doom. This gives you{' '}
          <span className="text-text">both sides argued properly</span>, then checks
          whether either side actually told you the truth.
        </p>
      </header>

      {/* A first-time visitor needs to know what this is before being asked to
          commit eight minutes to it. Three steps, plain language, no jargon. */}
      <div className="mb-8 grid gap-3 sm:grid-cols-3">
        {[
          {
            n: '1',
            title: 'You name a company',
            body: 'Type "Apple", no ticker symbols needed, and say whether you care about the next few weeks or the next few years.',
          },
          {
            n: '2',
            title: 'Three analysts dig in',
            body: 'One argues the stock will do well, one argues it won\'t, and one maps what could go wrong either way. They use real market data and SEC filings, not opinions.',
          },
          {
            n: '3',
            title: 'They argue, then get checked',
            body: 'Each attacks the other\'s weakest points. A senior analyst writes the verdict, and a separate checker verifies every number against its source.',
          },
        ].map((step) => (
          <div key={step.n} className="rounded-xl border border-line bg-surface/40 p-4">
            <div className="mb-1.5 flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-accent/15 text-[11px] font-semibold text-accent">
                {step.n}
              </span>
              <span className="text-sm font-medium">{step.title}</span>
            </div>
            <p className="text-xs leading-relaxed text-muted">{step.body}</p>
          </div>
        ))}
      </div>

      <div className="rounded-2xl border border-line bg-surface/70 p-6 shadow-2xl backdrop-blur">
        <label className="mb-2 block text-xs font-medium tracking-wide text-muted uppercase">
          Company name or ticker
        </label>
        <div className="relative">
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && start()}
            placeholder="Apple, Tesla, NVDA…"
            className="w-full rounded-xl border border-line bg-ink px-4 py-3.5 text-lg outline-none transition placeholder:text-faint focus:border-accent/60 focus:ring-2 focus:ring-accent/20"
          />
          {checking && (
            <span className="absolute top-1/2 right-4 -translate-y-1/2 text-xs text-faint">
              checking…
            </span>
          )}
        </div>

        {resolution && (
          <div className="mt-2 text-sm">
            {resolution.resolved ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-muted">{resolution.message}</span>
                {resolution.candidates.slice(0, 4).map((candidate) => (
                  <button
                    key={candidate.ticker}
                    onClick={() => setQuery(candidate.ticker)}
                    className={`rounded-md border px-2 py-0.5 font-mono text-xs transition ${
                      candidate.ticker === resolution.ticker
                        ? 'border-accent/50 bg-accent/10 text-accent'
                        : 'border-line text-muted hover:border-accent/40 hover:text-text'
                    }`}
                    title={`${candidate.name}${candidate.exchange ? ` · ${candidate.exchange}` : ''}`}
                  >
                    {candidate.ticker}
                  </button>
                ))}
              </div>
            ) : (
              <span className="text-warn">{resolution.message}</span>
            )}
          </div>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-faint">
          <span>Try:</span>
          {EXAMPLES.map((name) => (
            <button
              key={name}
              onClick={() => setQuery(name)}
              className="rounded-full border border-line px-2.5 py-1 text-muted transition hover:border-accent/40 hover:text-text"
            >
              {name}
            </button>
          ))}
        </div>

        <div className="mt-6">
          <label className="mb-2 block text-xs font-medium tracking-wide text-muted uppercase">
            Time horizon, this changes how the agents argue
          </label>
          <div className="grid gap-2 sm:grid-cols-3">
            {HORIZONS.map((option) => (
              <button
                key={option.value}
                onClick={() => setHorizon(option.value)}
                title={option.hint}
                className={`rounded-xl border px-3 py-3 text-left transition ${
                  horizon === option.value
                    ? 'border-accent/60 bg-accent/10'
                    : 'border-line bg-ink hover:border-line-soft hover:bg-surface-2'
                }`}
              >
                <div className="text-sm font-medium">{option.label}</div>
                <div className="text-[11px] text-faint">{option.sub}</div>
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs leading-relaxed text-faint">
            {HORIZONS.find((h) => h.value === horizon)?.hint}
          </p>
        </div>

        <div className="mt-5">
          {!showView ? (
            <button
              onClick={() => setShowView(true)}
              className="text-sm text-accent transition hover:text-text"
            >
              + Add your own view (optional)
            </button>
          ) : (
            <div className="animate-in">
              <label className="mb-2 block text-xs font-medium tracking-wide text-muted uppercase">
                Your own take, the agents will test it against the evidence
              </label>
              <textarea
                value={view}
                onChange={(e) => setView(e.target.value)}
                rows={2}
                placeholder="e.g. I think their new product will flop, or it just feels overpriced to me"
                className="w-full resize-none rounded-xl border border-line bg-ink px-4 py-3 text-sm outline-none transition placeholder:text-faint focus:border-accent/60 focus:ring-2 focus:ring-accent/20"
              />
              <p className="mt-1.5 text-xs text-faint">
                Whichever side agrees will pick it up; the other will push back with evidence.
              </p>
            </div>
          )}
        </div>

        <button
          onClick={() => start()}
          disabled={!canRun}
          className="mt-6 w-full rounded-xl bg-accent px-4 py-3.5 font-semibold text-ink transition hover:brightness-110 disabled:cursor-not-allowed disabled:bg-line disabled:text-faint"
        >
          {busy ? 'Running…' : 'Run the analysis'}
        </button>
        <p className="mt-2.5 text-center text-xs text-faint">
          Takes about 5-8 minutes. You can watch the agents work.
        </p>
      </div>

      {history.length > 0 && (
        <div className="mt-10">
          <h2 className="mb-3 text-xs font-medium tracking-wide text-muted uppercase">
            Previous runs
          </h2>
          <div className="divide-y divide-line-soft overflow-hidden rounded-xl border border-line bg-surface/50">
            {history.slice(0, 6).map((row) => (
              <button
                key={row.run_id}
                onClick={() => onOpenRun(row.run_id)}
                className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition hover:bg-surface-2"
              >
                <span className="w-14 font-mono text-accent">{row.ticker ?? ''}</span>
                <span className="flex-1 truncate text-muted">{row.company_name ?? row.run_id}</span>
                {row.claims_flagged ? (
                  <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[11px] text-warn">
                    {row.claims_flagged} flagged
                  </span>
                ) : null}
                <span className="text-xs text-faint">{row.confidence ?? row.stage}</span>
                <span className="w-28 text-right text-xs text-faint">
                  {row.created_at?.replace('T', ' ').slice(0, 16)}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      <p className="mt-8 text-center text-xs leading-relaxed text-faint">
        Research to help you think, not advice telling you what to do.
        <br />
        No buy, sell or hold recommendations are ever produced.
      </p>
    </div>
  )
}
