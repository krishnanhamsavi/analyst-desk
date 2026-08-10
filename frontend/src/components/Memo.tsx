import { useState } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { AutoTerms } from './Term'
import type { MemoPoint, RunPayload, SourceRef, VerificationFinding } from '../types'

const CONFIDENCE = {
  high: { label: 'Fairly confident', fill: 0.82, color: 'var(--color-bull)' },
  moderate: { label: 'Mixed', fill: 0.5, color: 'var(--color-warn)' },
  low: { label: 'Uncertain', fill: 0.24, color: 'var(--color-bear)' },
}

function Gauge({ level, reason }: { level: keyof typeof CONFIDENCE; reason: string }) {
  const config = CONFIDENCE[level]
  return (
    <div className="rounded-xl border border-line bg-surface/60 p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="text-xs tracking-wide text-muted uppercase">How confident</span>
        <span className="text-sm font-semibold" style={{ color: config.color }}>
          {config.label}
        </span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-ink">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${config.fill * 100}%`, background: config.color }}
        />
      </div>
      <p className="mt-2 text-xs leading-relaxed text-muted">
        <AutoTerms text={reason} />
      </p>
    </div>
  )
}

function PriceChart({ chart, ticker }: { chart: RunPayload['chart']; ticker: string | null }) {
  if (!chart?.series?.length) return null
  const series = chart.series
  const first = series[0].close
  const last = series[series.length - 1].close
  const up = last >= first
  const colour = up ? 'var(--color-bull)' : 'var(--color-bear)'
  const change = (((last - first) / first) * 100).toFixed(1)

  return (
    <div className="rounded-xl border border-line bg-surface/60 p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <span className="font-mono text-2xl font-semibold">{last?.toFixed(2)}</span>
          <span className="ml-2 text-sm" style={{ color: colour }}>
            {up ? '▲' : '▼'} {change}% over this window
          </span>
        </div>
        <div className="flex gap-4 text-xs text-faint">
          {chart.range_52w?.low != null && (
            <span>
              52-week range{' '}
              <span className="font-mono text-muted">
                {chart.range_52w.low} – {chart.range_52w.high}
              </span>
            </span>
          )}
          {chart.volatility_pct != null && (
            <span>
              volatility <span className="font-mono text-muted">{chart.volatility_pct}%</span>
            </span>
          )}
        </div>
      </div>

      <ResponsiveContainer width="100%" height={180}>
        <AreaChart data={series} margin={{ top: 4, right: 4, left: -18, bottom: 0 }}>
          <defs>
            <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={colour} stopOpacity={0.32} />
              <stop offset="100%" stopColor={colour} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="var(--color-line-soft)" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: 'var(--color-faint)', fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            minTickGap={48}
            tickFormatter={(d: string) => d.slice(0, 7)}
          />
          <YAxis
            domain={['dataMin', 'dataMax']}
            tick={{ fill: 'var(--color-faint)', fontSize: 10 }}
            tickLine={false}
            axisLine={false}
            width={52}
          />
          <Tooltip
            contentStyle={{
              background: 'var(--color-surface-2)',
              border: '1px solid var(--color-line)',
              borderRadius: 8,
              fontSize: 12,
            }}
            labelStyle={{ color: 'var(--color-muted)' }}
            formatter={(value) => [Number(value).toFixed(2), ticker ?? 'price']}
          />
          {chart.sma_200 != null && (
            <ReferenceLine
              y={chart.sma_200}
              stroke="var(--color-faint)"
              strokeDasharray="4 4"
              label={{ value: '200-day avg', fill: 'var(--color-faint)', fontSize: 10, position: 'insideTopLeft' }}
            />
          )}
          <Area
            type="monotone"
            dataKey="close"
            stroke={colour}
            strokeWidth={1.8}
            fill="url(#priceFill)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}

function Citation({ refId, sources }: { refId: string; sources: SourceRef[] }) {
  const [open, setOpen] = useState(false)
  const source = sources.find((s) => s.ref_id === refId)
  if (!source) {
    return (
      <span className="ml-1 rounded bg-bear/15 px-1 font-mono text-[10px] text-bear">
        {refId}?
      </span>
    )
  }
  return (
    <span className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="ml-1 rounded bg-accent/12 px-1.5 py-0.5 font-mono text-[10px] text-accent transition hover:bg-accent/25"
      >
        {refId}
      </button>
      {open && (
        <span className="absolute bottom-full left-0 z-50 mb-2 block w-96 rounded-lg border border-line bg-surface-2 p-3 shadow-2xl">
          <span className="mb-1 block text-xs font-semibold text-accent">{source.label}</span>
          {source.url && (
            <a
              href={source.url}
              target="_blank"
              rel="noreferrer"
              className="mb-2 block truncate text-[11px] text-muted underline hover:text-accent"
            >
              {source.url}
            </a>
          )}
          <span className="block max-h-44 overflow-auto rounded bg-ink p-2 font-mono text-[10px] leading-relaxed break-words text-muted">
            {JSON.stringify(source.detail, null, 1).slice(0, 1400)}
          </span>
        </span>
      )}
    </span>
  )
}

function PointRow({
  point,
  sources,
  side,
}: {
  point: MemoPoint
  sources: SourceRef[]
  side: 'bull' | 'bear'
}) {
  const unverified = point.point.includes('[unverified]')
  return (
    <li className="flex gap-2.5 py-2">
      <span className={`mt-0.5 shrink-0 text-sm ${side === 'bull' ? 'text-bull' : 'text-bear'}`}>
        {side === 'bull' ? '▲' : '▼'}
      </span>
      <span className="text-sm leading-relaxed">
        <AutoTerms text={point.point.replace(' [unverified]', '')} />
        <Citation refId={point.evidence_ref} sources={sources} />
        {unverified && (
          <span className="ml-1.5 rounded bg-bear/15 px-1.5 py-0.5 text-[10px] text-bear">
            unverified
          </span>
        )}
        {!point.survived_debate && !unverified && (
          <span className="ml-1.5 rounded bg-warn/15 px-1.5 py-0.5 text-[10px] text-warn">
            challenged in debate
          </span>
        )}
      </span>
    </li>
  )
}

function VerificationPanel({ report }: { report: RunPayload['verification'] }) {
  const [expanded, setExpanded] = useState(false)

  if (!report) {
    return (
      <section className="rounded-xl border border-bear/40 bg-bear/10 p-4">
        <h3 className="text-sm font-semibold text-bear">Verification did not run</h3>
        <p className="mt-1 text-xs leading-relaxed text-muted">
          The independent checker failed on this run, so the claims above have{' '}
          <strong>not</strong> been verified against the source data. Treat them with
          caution.
        </p>
      </section>
    )
  }

  const flagged = report.findings.filter((f) => f.verdict !== 'supported')
  const tone =
    report.overall_verdict === 'clean'
      ? { border: 'border-bull/40', bg: 'bg-bull/8', text: 'text-bull', label: 'All claims check out' }
      : report.overall_verdict === 'minor_issues'
        ? { border: 'border-warn/40', bg: 'bg-warn/8', text: 'text-warn', label: 'Minor issues found' }
        : { border: 'border-bear/40', bg: 'bg-bear/8', text: 'text-bear', label: 'Issues found' }

  return (
    <section className={`rounded-xl border ${tone.border} ${tone.bg} p-4`}>
      <div className="flex items-center justify-between">
        <h3 className={`text-sm font-semibold ${tone.text}`}>Verification — {tone.label}</h3>
        <span className="text-xs text-muted">
          {report.findings.length} claims checked · {flagged.length} flagged
        </span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-muted">{report.summary}</p>

      {flagged.length > 0 && (
        <>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="mt-3 text-xs text-accent transition hover:text-text"
          >
            {expanded ? 'Hide' : 'Show'} the {flagged.length} flagged claim
            {flagged.length === 1 ? '' : 's'}
          </button>
          {expanded && (
            <ul className="mt-3 space-y-3">
              {flagged.map((finding: VerificationFinding, i) => (
                <li key={i} className="animate-in rounded-lg border border-line bg-ink/60 p-3">
                  <div className="mb-1 flex items-center gap-2">
                    <span
                      className={`rounded px-1.5 py-0.5 text-[10px] uppercase ${
                        finding.verdict === 'unsupported'
                          ? 'bg-bear/20 text-bear'
                          : 'bg-warn/20 text-warn'
                      }`}
                    >
                      {finding.verdict}
                    </span>
                    <span className="font-mono text-[10px] text-faint">{finding.evidence_ref}</span>
                  </div>
                  <p className="text-xs leading-relaxed text-text">"{finding.claim}"</p>
                  <p className="mt-1.5 text-xs leading-relaxed text-muted">{finding.explanation}</p>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <p className="mt-3 text-[11px] leading-relaxed text-faint">
        The checker never saw the debate — only the memo and the raw source records, so it
        can't be persuaded by the argument it is checking.
      </p>
    </section>
  )
}

export function Memo({ run, beginner }: { run: RunPayload; beginner: boolean }) {
  const memo = run.memo
  if (!memo) return null
  const sources = run.sources ?? []

  return (
    <div className="space-y-4">
      <PriceChart chart={run.chart} ticker={run.ticker} />

      <section className="rounded-xl border border-line bg-surface/60 p-5">
        <h2 className="mb-2 text-xs tracking-wide text-muted uppercase">In plain English</h2>
        <p className="text-[15px] leading-relaxed">
          <AutoTerms text={memo.plain_summary} />
        </p>
      </section>

      <Gauge level={memo.confidence} reason={memo.confidence_reasoning} />

      <section className="rounded-xl border border-warn/30 bg-warn/5 p-5">
        <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-warn">
          <span>⚠</span> Key risks
        </h2>
        <ul className="space-y-2.5">
          {memo.key_risks.map((risk, i) => (
            <li key={i} className="text-sm leading-relaxed text-text">
              <AutoTerms text={risk} />
            </li>
          ))}
        </ul>
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-xl border border-line bg-surface/60 p-5">
          <h2 className="mb-1 text-sm font-semibold text-bull">The case for</h2>
          <ul className="divide-y divide-line-soft">
            {memo.bull_case.map((point, i) => (
              <PointRow key={i} point={point} sources={sources} side="bull" />
            ))}
          </ul>
        </section>

        <section className="rounded-xl border border-line bg-surface/60 p-5">
          <h2 className="mb-1 text-sm font-semibold text-bear">The case against</h2>
          <ul className="divide-y divide-line-soft">
            {memo.bear_case.map((point, i) => (
              <PointRow key={i} point={point} sources={sources} side="bear" />
            ))}
          </ul>
        </section>
      </div>

      <section className="rounded-xl border border-line bg-surface/60 p-5">
        <h2 className="mb-1 text-sm font-semibold">What would have to be true</h2>
        <p className="mb-4 text-xs text-faint">
          The conditions each side needs. These are checkable later — that's the point.
        </p>
        <div className="grid gap-5 sm:grid-cols-2">
          <div>
            <h3 className="mb-2 text-xs tracking-wide text-bull uppercase">
              For the case FOR to win
            </h3>
            <ul className="space-y-2">
              {memo.bull_needs_to_be_true.map((item, i) => (
                <li key={i} className="text-sm leading-relaxed text-muted">
                  <AutoTerms text={item} />
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="mb-2 text-xs tracking-wide text-bear uppercase">
              For the case AGAINST to win
            </h3>
            <ul className="space-y-2">
              {memo.bear_needs_to_be_true.map((item, i) => (
                <li key={i} className="text-sm leading-relaxed text-muted">
                  <AutoTerms text={item} />
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      {!beginner && (
        <section className="animate-in rounded-xl border border-line bg-surface/60 p-5">
          <h2 className="mb-2 text-sm font-semibold">How the debate went</h2>
          <p className="text-sm leading-relaxed text-muted">
            <AutoTerms text={memo.how_the_debate_went} />
          </p>
        </section>
      )}

      {memo.user_view_assessment && (
        <section className="rounded-xl border border-accent/30 bg-accent/5 p-5">
          <h2 className="mb-1 text-sm font-semibold text-accent">Your view, tested</h2>
          {run.user_view && (
            <p className="mb-2 text-xs text-faint italic">You said: "{run.user_view}"</p>
          )}
          <p className="text-sm leading-relaxed">
            <AutoTerms text={memo.user_view_assessment} />
          </p>
        </section>
      )}

      <section className="rounded-xl border border-line bg-surface-2 p-5">
        <h2 className="mb-2 text-xs tracking-wide text-muted uppercase">
          What this means for you
        </h2>
        <p className="text-[15px] leading-relaxed">{memo.what_this_means_for_you}</p>
        <p className="mt-3 border-t border-line pt-3 text-[11px] text-faint">
          Research to help you think — not advice telling you what to do. No buy, sell or
          hold recommendation is given or implied.
        </p>
      </section>

      <VerificationPanel report={run.verification} />

      {!beginner && (
        <details className="animate-in rounded-xl border border-line bg-surface/60 p-5">
          <summary className="cursor-pointer text-sm font-semibold">
            Sources ({sources.length})
          </summary>
          <ul className="mt-3 space-y-1.5">
            {sources.map((source) => (
              <li key={source.ref_id} className="text-xs">
                <span className="mr-2 font-mono text-accent">[{source.ref_id}]</span>
                <span className="text-muted">{source.label}</span>
                {source.url && (
                  <a
                    href={source.url}
                    target="_blank"
                    rel="noreferrer"
                    className="ml-2 text-faint underline hover:text-accent"
                  >
                    link
                  </a>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      {run.degraded && run.degraded.length > 0 && !beginner && (
        <details className="rounded-xl border border-line bg-surface/40 p-4">
          <summary className="cursor-pointer text-xs text-faint">
            Steps that degraded during this run ({run.degraded.length})
          </summary>
          <ul className="mt-2 space-y-1">
            {run.degraded.map((note, i) => (
              <li key={i} className="font-mono text-[11px] text-faint">
                {note}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
