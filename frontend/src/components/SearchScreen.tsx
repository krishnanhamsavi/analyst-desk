import { useEffect, useState } from 'react'
import { api } from '../api'
import type { HistoryRow, Resolution } from '../types'

const EXAMPLES = ['Apple', 'Nvidia', 'Tesla', 'Coca-Cola', 'Microsoft']

const HORIZONS = [
  {
    value: 'short',
    label: 'Next few weeks',
    sub: 'momentum and news',
    hint: 'Business quality barely moves a price in weeks. The analysts weight recent price action and live catalysts, and pull tighter charts to judge them.',
  },
  {
    value: 'medium',
    label: '6 to 12 months',
    sub: 'the balanced view',
    hint: 'Long enough that results matter, short enough that sentiment still rules. Whether earnings beat expectations usually decides it.',
  },
  {
    value: 'long',
    label: '3 to 5 years',
    sub: 'the business itself',
    hint: 'Momentum becomes noise. What matters is the moat, reinvestment and whether the business compounds, so the analysts pull five years of history.',
  },
]

/** The desk, in order. Doubles as the explanation of what the product is. */
const PIPELINE = [
  { n: '1', title: 'Reads the filings', body: 'Live prices, audited SEC financials, the annual report, news and a peer group.', tone: 'text-accent' },
  { n: '2', title: 'Two analysts argue', body: 'One builds the case for, one the case against. Neither can see the other while researching.', tone: 'text-bull' },
  { n: '3', title: 'They attack each other', body: 'Each hunts the other for unsupported claims, cherry-picked windows and overreach.', tone: 'text-bear' },
  { n: '4', title: 'A referee decides', body: 'Weighs which arguments survived and writes one page in plain English.', tone: 'text-text' },
  { n: '5', title: 'A checker verifies it', body: 'Never saw the debate. Re-checks every number against its source and flags what fails.', tone: 'text-warn' },
]

const CHECKS = [
  ['Valuation', 'Cheap or expensive, against its own history and its peers'],
  ['Growth', 'Revenue and earnings, and whether they are speeding up or slowing'],
  ['Profitability', 'Margins, cash generation, return on capital'],
  ['Balance sheet', 'Debt, cash, and whether it survives a bad year'],
  ['Moat', 'What stops a competitor taking the business'],
  ['Momentum', 'Price against its averages, distance from highs and lows'],
  ['Catalysts', 'What is coming, and what the filings admit could go wrong'],
]

const SOURCES = [
  ['SEC EDGAR', 'Annual and quarterly reports, plus the Risk Factors the company must disclose'],
  ['SEC XBRL', 'Audited financials exactly as filed, not scraped from a website'],
  ['Market data', 'Prices, volatility, drawdowns, moving averages'],
  ['Peer group', 'Comparable companies, so a multiple means something'],
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

  // Resolving as they type means "Did you mean Apple Inc.?" arrives before they
  // commit five minutes to the wrong company.
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

  return (
    <div className="relative">
      {/* ---------------------------------------------------------- hero */}
      <div className="relative overflow-hidden">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 -top-40 h-96 opacity-60"
          style={{
            background:
              'radial-gradient(60% 60% at 50% 50%, rgba(110,168,254,0.16) 0%, transparent 70%)',
          }}
        />

        <div className="relative mx-auto max-w-5xl px-6 pt-16 pb-10">
          <div className="mb-5 flex justify-center">
            <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface/70 px-3 py-1 text-[11px] tracking-[0.14em] text-muted uppercase backdrop-blur">
              <span className="h-1.5 w-1.5 animate-working rounded-full bg-bull" />
              Six agents · every claim sourced · every number checked
            </span>
          </div>

          <h1 className="text-center text-4xl leading-[1.08] font-semibold tracking-tight sm:text-6xl">
            Every stock article is
            <br />
            <span className="text-bull">hype</span> or <span className="text-bear">doom</span>.
          </h1>
          <p className="mx-auto mt-5 max-w-2xl text-center text-base leading-relaxed text-muted sm:text-lg">
            Analyst Desk argues both sides properly, then checks whether either side
            told you the truth. You get one page you can actually trust, with the
            unverifiable parts labelled as unverifiable.
          </p>

          {/* ------------------------------------------------------ search */}
          <div className="mx-auto mt-9 max-w-2xl">
            <div className="rounded-2xl border border-line bg-surface/80 p-5 shadow-[0_20px_60px_-20px_rgba(0,0,0,0.8)] backdrop-blur">
              <div className="relative">
                <input
                  autoFocus
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && canRun && onStart(query.trim(), horizon, view.trim() || null)}
                  placeholder="Type any company. Apple, Nvidia, your employer..."
                  className="w-full rounded-xl border border-line bg-ink px-4 py-4 text-lg outline-none transition placeholder:text-faint focus:border-accent/60 focus:ring-4 focus:ring-accent/10"
                />
                {checking && (
                  <span className="absolute top-1/2 right-4 -translate-y-1/2 text-xs text-faint">
                    finding it...
                  </span>
                )}
              </div>

              {resolution && (
                <div className="mt-2.5 flex flex-wrap items-center gap-2 text-sm">
                  {resolution.resolved ? (
                    <>
                      <span className="text-muted">{resolution.message}</span>
                      {resolution.candidates.slice(0, 4).map((c) => (
                        <button
                          key={c.ticker}
                          onClick={() => setQuery(c.ticker)}
                          title={`${c.name}${c.exchange ? ` on ${c.exchange}` : ''}`}
                          className={`rounded-md border px-2 py-0.5 font-mono text-xs transition ${
                            c.ticker === resolution.ticker
                              ? 'border-accent/50 bg-accent/10 text-accent'
                              : 'border-line text-muted hover:border-accent/40 hover:text-text'
                          }`}
                        >
                          {c.ticker}
                        </button>
                      ))}
                    </>
                  ) : (
                    <span className="text-warn">{resolution.message}</span>
                  )}
                </div>
              )}

              <div className="mt-3 flex flex-wrap items-center gap-1.5 text-xs">
                <span className="text-faint">Try</span>
                {EXAMPLES.map((name) => (
                  <button
                    key={name}
                    onClick={() => setQuery(name)}
                    className="rounded-full border border-line px-2.5 py-1 text-muted transition hover:border-accent/40 hover:bg-accent/5 hover:text-text"
                  >
                    {name}
                  </button>
                ))}
              </div>

              <div className="mt-5">
                <div className="mb-2 flex items-baseline justify-between">
                  <span className="text-xs font-medium tracking-wide text-muted uppercase">
                    How far ahead do you care about?
                  </span>
                  <span className="text-[11px] text-faint">changes what counts as evidence</span>
                </div>
                <div className="grid gap-2 sm:grid-cols-3">
                  {HORIZONS.map((option) => (
                    <button
                      key={option.value}
                      onClick={() => setHorizon(option.value)}
                      className={`group rounded-xl border px-3 py-2.5 text-left transition ${
                        horizon === option.value
                          ? 'border-accent/60 bg-accent/10 shadow-[inset_0_1px_0_rgba(255,255,255,0.04)]'
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

              <div className="mt-4">
                {!showView ? (
                  <button
                    onClick={() => setShowView(true)}
                    className="text-sm text-accent transition hover:text-text"
                  >
                    + Have a hunch? Add it and they will test it
                  </button>
                ) : (
                  <div className="animate-in">
                    <label className="mb-1.5 block text-xs font-medium tracking-wide text-muted uppercase">
                      Your own take
                    </label>
                    <textarea
                      value={view}
                      onChange={(e) => setView(e.target.value)}
                      rows={2}
                      placeholder="e.g. I think their new product will flop, or it just feels overpriced"
                      className="w-full resize-none rounded-xl border border-line bg-ink px-4 py-3 text-sm outline-none transition placeholder:text-faint focus:border-accent/60"
                    />
                    <p className="mt-1.5 text-xs text-faint">
                      Whichever side agrees will pick it up. The other will push back with evidence.
                    </p>
                  </div>
                )}
              </div>

              <button
                onClick={() => canRun && onStart(query.trim(), horizon, view.trim() || null)}
                disabled={!canRun}
                className="mt-5 w-full rounded-xl bg-accent px-4 py-3.5 text-[15px] font-semibold text-ink transition hover:brightness-110 disabled:cursor-not-allowed disabled:bg-line disabled:text-faint"
              >
                {busy ? 'Running the desk...' : 'Run the analysis'}
              </button>
              <p className="mt-2 text-center text-xs text-faint">
                About 5 minutes. You can watch every step happen.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* ------------------------------------------------------ pipeline */}
      <section className="mx-auto max-w-6xl px-6 py-12">
        <h2 className="mb-1 text-center text-xl font-semibold">What actually happens</h2>
        <p className="mx-auto mb-8 max-w-xl text-center text-sm text-muted">
          Not one model writing an essay. Five separate jobs, deliberately kept apart.
        </p>

        <div className="grid gap-3 md:grid-cols-5">
          {PIPELINE.map((step, i) => (
            <div key={step.n} className="relative">
              <div className="h-full rounded-xl border border-line bg-surface/40 p-4 transition hover:border-line-soft hover:bg-surface/70">
                <div className={`mb-2 font-mono text-xs ${step.tone}`}>{step.n}</div>
                <div className="mb-1.5 text-sm font-medium">{step.title}</div>
                <p className="text-xs leading-relaxed text-muted">{step.body}</p>
              </div>
              {i < PIPELINE.length - 1 && (
                <div className="absolute top-1/2 -right-1.5 z-10 hidden h-px w-3 bg-line md:block" />
              )}
            </div>
          ))}
        </div>
      </section>

      {/* --------------------------------------------------------- proof */}
      <section className="mx-auto max-w-4xl px-6 pb-12">
        <div className="overflow-hidden rounded-2xl border border-warn/30 bg-gradient-to-br from-warn/[0.07] to-transparent">
          <div className="border-b border-warn/20 px-6 py-3">
            <span className="text-[11px] font-medium tracking-[0.14em] text-warn uppercase">
              Caught in a real run
            </span>
          </div>
          <div className="px-6 py-5">
            <p className="mb-3 text-sm leading-relaxed text-muted">
              While analysing Coca-Cola, the memo claimed the company's annual report warns
              about consumers switching to cheaper supermarket own-brand drinks. It reads
              exactly like something a 10-K would say.
            </p>
            <div className="mb-3 rounded-lg border border-line bg-ink/70 p-4">
              <div className="mb-1.5 flex items-center gap-2">
                <span className="rounded bg-bear/20 px-1.5 py-0.5 text-[10px] tracking-wide text-bear uppercase">
                  misrepresented
                </span>
                <span className="font-mono text-[10px] text-faint">S21</span>
              </div>
              <p className="text-sm leading-relaxed text-text">
                The filing mentions inflation and recession affecting affordability. It says
                nothing about own-brand competitors. The claim was invented.
              </p>
            </div>
            <p className="text-xs leading-relaxed text-faint">
              A chatbot would have told you that confidently, with no way for you to know.
              This flagged it before you read it, because the checker only ever sees the
              claims and the raw filing, never the argument that produced them.
            </p>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------ checks + sources */}
      <section className="mx-auto max-w-6xl px-6 pb-14">
        <div className="grid gap-6 lg:grid-cols-2">
          <div>
            <h2 className="mb-1 text-lg font-semibold">What it examines</h2>
            <p className="mb-4 text-sm text-muted">
              The same checklist a real analyst works through, weighted by your horizon.
            </p>
            <div className="divide-y divide-line-soft overflow-hidden rounded-xl border border-line bg-surface/40">
              {CHECKS.map(([name, detail]) => (
                <div key={name} className="flex gap-3 px-4 py-2.5">
                  <span className="w-24 shrink-0 text-sm font-medium">{name}</span>
                  <span className="text-xs leading-relaxed text-muted">{detail}</span>
                </div>
              ))}
            </div>
          </div>

          <div>
            <h2 className="mb-1 text-lg font-semibold">Where the facts come from</h2>
            <p className="mb-4 text-sm text-muted">
              Every number in the memo links back to one of these, and you can click it open.
            </p>
            <div className="space-y-2">
              {SOURCES.map(([name, detail]) => (
                <div
                  key={name}
                  className="rounded-xl border border-line bg-surface/40 px-4 py-3 transition hover:border-line-soft"
                >
                  <div className="mb-0.5 text-sm font-medium text-accent">{name}</div>
                  <p className="text-xs leading-relaxed text-muted">{detail}</p>
                </div>
              ))}
            </div>

            <div className="mt-4 rounded-xl border border-line bg-surface/40 px-4 py-3">
              <div className="mb-1 text-sm font-medium">And afterwards, you can ask</div>
              <p className="text-xs leading-relaxed text-muted">
                A chat sits under every memo. Ask why something is risky, or say "explain
                this simply". It answers from the same sources and says "I don't have data
                on that" rather than guessing.
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ------------------------------------------------------- history */}
      {history.length > 0 && (
        <section className="mx-auto max-w-4xl px-6 pb-14">
          <h2 className="mb-3 text-xs font-medium tracking-wide text-muted uppercase">
            Your previous runs
          </h2>
          <div className="divide-y divide-line-soft overflow-hidden rounded-xl border border-line bg-surface/40">
            {history.slice(0, 6).map((row) => (
              <button
                key={row.run_id}
                onClick={() => onOpenRun(row.run_id)}
                className="flex w-full items-center gap-3 px-4 py-3 text-left text-sm transition hover:bg-surface-2"
              >
                <span className="w-14 font-mono text-accent">{row.ticker ?? '?'}</span>
                <span className="flex-1 truncate text-muted">
                  {row.company_name ?? row.run_id}
                </span>
                {row.claims_flagged ? (
                  <span className="rounded bg-warn/15 px-1.5 py-0.5 text-[11px] text-warn">
                    {row.claims_flagged} flagged
                  </span>
                ) : null}
                <span className="text-xs text-faint">{row.confidence ?? row.stage}</span>
                <span className="hidden w-28 text-right text-xs text-faint sm:block">
                  {row.created_at?.replace('T', ' ').slice(0, 16)}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      <footer className="border-t border-line-soft px-6 py-8">
        <p className="mx-auto max-w-xl text-center text-xs leading-relaxed text-faint">
          Research to help you think, not advice telling you what to do. No buy, sell or
          hold recommendation is ever produced, and nothing here predicts a price.
        </p>
      </footer>
    </div>
  )
}
