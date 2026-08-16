import type { DeskEvent, HistoryRow, Resolution, RunPayload } from './types'

/**
 * Demo mode: the whole product, served as static files.
 *
 * A public deployment with live runs is a liability. Each one takes five minutes
 * and costs real money, so an open URL invites a stranger to spend the API
 * budget in a loop. But every run is fully recorded, including the event stream,
 * so a finished one can be replayed exactly as it happened.
 *
 * The visitor sees the analysts work, the debate, the memo and the verifier's
 * findings. It costs nothing and cannot be abused. Live runs stay local.
 */
export const DEMO_MODE = import.meta.env.VITE_DEMO_MODE === 'true'

let demoIndex: HistoryRow[] | null = null

async function loadDemoIndex(): Promise<HistoryRow[]> {
  if (demoIndex) return demoIndex
  const res = await fetch(`${import.meta.env.BASE_URL}demo/index.json`)
  demoIndex = res.ok ? await res.json() : []
  return demoIndex!
}

async function loadDemoRun(runId: string): Promise<RunPayload & { events?: DeskEvent[] }> {
  const res = await fetch(`${import.meta.env.BASE_URL}demo/${runId}.json`)
  if (!res.ok) throw new Error('That run is not part of the demo.')
  return res.json()
}

/** Find the demo run that best matches what the visitor asked for. */
async function matchDemoRun(query: string, horizon: string): Promise<HistoryRow | null> {
  const index = await loadDemoIndex()
  if (!index.length) return null
  const q = query.trim().toLowerCase()

  const scored = index.map((row) => {
    let score = 0
    const ticker = (row.ticker ?? '').toLowerCase()
    const name = (row.company_name ?? '').toLowerCase()
    if (ticker === q || name === q) score += 100
    else if (name.startsWith(q) || ticker.startsWith(q)) score += 60
    else if (name.includes(q) || q.includes(ticker)) score += 30
    if (row.horizon === horizon) score += 10
    return { row, score }
  })

  scored.sort((a, b) => b.score - a.score)
  return scored[0].score > 0 ? scored[0].row : null
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text()
    let message = body
    try {
      message = JSON.parse(body).detail ?? body
    } catch {
      /* plain-text error */
    }
    throw new Error(message || `Request failed (${res.status})`)
  }
  return res.json() as Promise<T>
}

export const api = {
  health: () => fetch('/api/health').then(json<Record<string, unknown>>),

  horizons: () =>
    fetch('/api/horizons').then(
      json<{ default: string; options: { value: string; label: string; emphasis: string }[] }>,
    ),

  async resolve(query: string): Promise<Resolution> {
    if (DEMO_MODE) {
      const match = await matchDemoRun(query, 'medium')
      return match
        ? {
            query, resolved: true, ticker: match.ticker, name: match.company_name,
            needs_confirmation: false, candidates: [],
            message: `Resolved to ${match.company_name} (${match.ticker}).`,
          }
        : {
            query, resolved: false, ticker: null, name: null,
            needs_confirmation: false, candidates: [],
            message: 'This demo has a fixed set of companies. Pick one of the examples below.',
          }
    }
    return fetch('/api/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    }).then(json<Resolution>)
  },

  async startRun(query: string, horizon: string, userView: string | null) {
    if (DEMO_MODE) {
      const match = await matchDemoRun(query, horizon)
      if (!match) throw new Error('This demo covers a fixed set of companies.')
      return { run_id: match.run_id, status: 'running', stream_url: '' }
    }
    return fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, horizon, user_view: userView }),
    }).then(json<{ run_id: string; status: string; stream_url: string }>)
  },

  async getRun(runId: string): Promise<RunPayload> {
    if (DEMO_MODE) return loadDemoRun(runId)
    return fetch(`/api/runs/${runId}`).then(json<RunPayload>)
  },

  async history(limit = 20): Promise<{ runs: HistoryRow[] }> {
    if (DEMO_MODE) return { runs: (await loadDemoIndex()).slice(0, limit) }
    return fetch(`/api/runs?limit=${limit}`).then(json<{ runs: HistoryRow[] }>)
  },

  /**
   * Live event stream. Returns an unsubscribe function.
   *
   * The server replays everything that already happened before sending live
   * events, so a late connection or a page refresh mid-run still shows the
   * whole story rather than an empty screen.
   */
  streamRun(
    runId: string,
    onEvent: (event: DeskEvent) => void,
    onClose: (status: string) => void,
  ): () => void {
    // In demo mode there is no socket. The recorded events are re-emitted on
    // their original timing, compressed so the whole run plays in about a
    // minute. The visitor watches the same thing a live run shows, including
    // the pauses where an analyst was thinking.
    if (DEMO_MODE) {
      const timers: number[] = []
      let cancelled = false

      loadDemoRun(runId)
        .then((run) => {
          if (cancelled) return
          const events = (run.events ?? []) as (DeskEvent & { replay_offset_s?: number })[]
          events.forEach((event) => {
            const delay = (event.replay_offset_s ?? 0) * 1000
            timers.push(window.setTimeout(() => !cancelled && onEvent(event), delay))
          })
          const last = events.length ? (events[events.length - 1].replay_offset_s ?? 0) : 0
          timers.push(
            window.setTimeout(() => !cancelled && onClose('done'), last * 1000 + 700),
          )
        })
        .catch(() => !cancelled && onClose('failed'))

      return () => {
        cancelled = true
        timers.forEach(clearTimeout)
      }
    }

    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${protocol}://${location.host}/ws/runs/${runId}`)
    let closed = false

    socket.onmessage = (message) => {
      const event = JSON.parse(message.data) as DeskEvent
      if (event.type === 'heartbeat') return
      if (event.type === 'stream_closed') {
        closed = true
        onClose(event.data?.status ?? 'done')
        return
      }
      onEvent(event)
    }

    socket.onerror = () => {
      if (!closed) onClose('failed')
    }

    return () => {
      closed = true
      socket.close()
    }
  },

  /** Follow-up question. Streams the answer back chunk by chunk. */
  async chat(
    runId: string,
    question: string,
    history: { role: string; content: string }[],
    onChunk: (text: string) => void,
  ): Promise<void> {
    if (DEMO_MODE) {
      // Answering a new question needs a model call, which is exactly what a
      // static demo cannot do. Say so plainly rather than faking an answer.
      onChunk(
        "The follow-up chat needs a live model, so it is switched off in this " +
          'public demo. Everything else here is a real recorded run: the memo, ' +
          'the sources and the verification are exactly what the desk produced.' +
          '\n\nRun it locally to ask questions of your own.',
      )
      return
    }

    const res = await fetch(`/api/runs/${runId}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, history }),
    })
    if (!res.ok || !res.body) throw new Error(await res.text())

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      onChunk(decoder.decode(value, { stream: true }))
    }
  },
}
