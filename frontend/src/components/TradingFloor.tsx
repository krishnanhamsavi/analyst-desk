import { useEffect, useMemo, useRef } from 'react'
import type { DeskEvent } from '../types'

const STAGES = [
  { key: 'resolve', label: 'Identify' },
  { key: 'gather', label: 'Gather data' },
  { key: 'research', label: 'Research' },
  { key: 'debate', label: 'Debate' },
  { key: 'synthesize', label: 'Synthesise' },
  { key: 'verify', label: 'Verify' },
]

const AGENTS = [
  {
    key: 'Bull',
    label: 'The Bull',
    role: 'Argues the case for',
    accent: 'text-bull',
    ring: 'border-bull/30',
    glow: 'bg-bull',
  },
  {
    key: 'Bear',
    label: 'The Bear',
    role: 'Argues the case against',
    accent: 'text-bear',
    ring: 'border-bear/30',
    glow: 'bg-bear',
  },
  {
    key: 'Risk',
    label: 'Risk Manager',
    role: 'Maps what could go wrong',
    accent: 'text-warn',
    ring: 'border-warn/30',
    glow: 'bg-warn',
  },
]

interface Props {
  events: DeskEvent[]
  company: string
  ticker: string | null
  status: string
}

/** Turns the raw event stream into one readable line per agent action. */
function describe(event: DeskEvent): string | null {
  const { type, data } = event
  switch (type) {
    case 'agent_started':
      return `Reading the briefing pack…`
    case 'tool_called': {
      const args = data.args && Object.keys(data.args).length
        ? ` (${Object.entries(data.args).map(([k, v]) => `${k}: ${v}`).join(', ')})`
        : ''
      return `Pulling ${String(data.tool).replace(/^get_/, '').replace(/_/g, ' ')}${args}`
    }
    case 'tool_result':
      return data.ok
        ? `Got it${data.refs?.length ? ` — sources ${data.refs.join(', ')}` : ''}`
        : `No data: ${data.error}`
    case 'agent_thinking':
      return String(data.text || '').trim().slice(0, 400)
    case 'agent_finished':
      return `Finished — ${data.tool_calls} tool call${data.tool_calls === 1 ? '' : 's'}, ${data.elapsed_s}s`
    case 'error':
      return `Problem: ${data.message}`
    default:
      return null
  }
}

function AgentColumn({
  agent,
  events,
  active,
}: {
  agent: (typeof AGENTS)[number]
  events: DeskEvent[]
  active: boolean
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const mine = events.filter((e) => e.agent === agent.key)
  const done = mine.some((e) => e.type === 'agent_finished')

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [mine.length])

  const state = done ? 'done' : active ? 'working' : 'waiting'

  return (
    <div
      className={`flex min-h-0 flex-col rounded-xl border bg-surface/60 ${
        active ? agent.ring : 'border-line'
      }`}
    >
      <div className="flex items-center justify-between border-b border-line-soft px-4 py-3">
        <div>
          <div className={`text-sm font-semibold ${agent.accent}`}>{agent.label}</div>
          <div className="text-[11px] text-faint">{agent.role}</div>
        </div>
        <div className="flex items-center gap-1.5 text-[11px] text-muted">
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              state === 'done' ? 'bg-bull' : state === 'working' ? `${agent.glow} animate-working` : 'bg-faint'
            }`}
          />
          {state === 'done' ? 'done' : state === 'working' ? 'working' : 'waiting'}
        </div>
      </div>

      <div ref={scroller} className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {mine.length === 0 && (
          <p className="px-1 py-6 text-center text-xs text-faint">Waiting its turn…</p>
        )}
        {mine.map((event, i) => {
          const text = describe(event)
          if (!text) return null
          const isThought = event.type === 'agent_thinking'
          const isTool = event.type === 'tool_called' || event.type === 'tool_result'
          return (
            <div
              key={event.event_id ?? i}
              className={`animate-in rounded-lg px-2.5 py-2 text-xs leading-relaxed ${
                isThought
                  ? 'bg-surface-2/70 text-muted'
                  : isTool
                    ? 'font-mono text-[11px] text-faint'
                    : 'text-muted'
              }`}
            >
              {text}
            </div>
          )
        })}
        {active && !done && (
          <div className="shimmer h-1 rounded-full" />
        )}
      </div>
    </div>
  )
}

export function TradingFloor({ events, company, ticker, status }: Props) {
  const stage = useMemo(() => {
    const stageEvents = events.filter((e) => e.type === 'run_stage')
    return stageEvents.length ? String(stageEvents[stageEvents.length - 1].data.stage) : 'resolve'
  }, [events])

  const stageIndex = Math.max(0, STAGES.findIndex((s) => s.key === stage))
  const debateEvents = events.filter((e) => e.type === 'debate_round')
  const inResearch = stage === 'research'

  const moderatorEvents = events.filter((e) => e.agent === 'Moderator')
  const checkerEvents = events.filter((e) => e.agent === 'FactChecker')

  return (
    <div className="mx-auto flex h-full max-w-7xl flex-col gap-4 px-4 py-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">
            {company}{' '}
            {ticker && <span className="font-mono text-base text-accent">{ticker}</span>}
          </h1>
          <p className="text-xs text-muted">
            The desk is working. Everything below is happening live.
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {STAGES.map((s, i) => (
            <div key={s.key} className="flex items-center gap-1.5">
              <div
                className={`rounded-md px-2.5 py-1 text-[11px] transition ${
                  i < stageIndex
                    ? 'bg-bull/10 text-bull'
                    : i === stageIndex
                      ? 'bg-accent/15 text-accent'
                      : 'text-faint'
                }`}
              >
                {s.label}
              </div>
              {i < STAGES.length - 1 && <div className="h-px w-3 bg-line" />}
            </div>
          ))}
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-3">
        {AGENTS.map((agent) => (
          <AgentColumn
            key={agent.key}
            agent={agent}
            events={events}
            active={
              inResearch || stage === 'debate'
                ? events.some(
                    (e) =>
                      e.agent === agent.key &&
                      e.type === 'agent_started' &&
                      !events.some(
                        (f) =>
                          f.agent === agent.key &&
                          f.type === 'agent_finished' &&
                          new Date(f.ts) > new Date(e.ts),
                      ),
                  )
                : false
            }
          />
        ))}
      </div>

      {debateEvents.length > 0 && (
        <div className="animate-in rounded-xl border border-line bg-surface/60 p-4">
          <h2 className="mb-2 text-sm font-semibold">The debate</h2>
          {debateEvents.map((event, i) => (
            <p key={i} className="text-xs text-muted">
              {event.data.note}
              {event.data.bull_critiques !== undefined && (
                <>
                  {' — '}
                  <span className="text-bull">{event.data.bull_critiques} challenges from the Bull</span>
                  {', '}
                  <span className="text-bear">{event.data.bear_critiques} from the Bear</span>
                  {event.data.concessions > 0 && (
                    <span className="text-accent">
                      {', '}
                      {event.data.concessions} point{event.data.concessions === 1 ? '' : 's'} conceded
                    </span>
                  )}
                </>
              )}
            </p>
          ))}
        </div>
      )}

      {(moderatorEvents.length > 0 || checkerEvents.length > 0) && (
        <div className="grid animate-in gap-3 sm:grid-cols-2">
          {[
            { label: 'Moderator', sub: 'weighing both cases', list: moderatorEvents },
            { label: 'Fact-Checker', sub: 'verifying every claim', list: checkerEvents },
          ]
            .filter((panel) => panel.list.length > 0)
            .map((panel) => {
              const finished = panel.list.some((e) => e.type === 'agent_finished')
              return (
                <div key={panel.label} className="rounded-xl border border-line bg-surface/60 px-4 py-3">
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-sm font-semibold">{panel.label}</div>
                      <div className="text-[11px] text-faint">{panel.sub}</div>
                    </div>
                    <span
                      className={`h-1.5 w-1.5 rounded-full ${
                        finished ? 'bg-bull' : 'animate-working bg-accent'
                      }`}
                    />
                  </div>
                </div>
              )
            })}
        </div>
      )}

      {status === 'failed' && (
        <div className="rounded-xl border border-bear/40 bg-bear/10 px-4 py-3 text-sm text-bear">
          The run could not be completed. Check the details below or try again.
        </div>
      )}
    </div>
  )
}
