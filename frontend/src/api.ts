import type { DeskEvent, HistoryRow, Resolution, RunPayload } from './types'

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

  resolve: (query: string) =>
    fetch('/api/resolve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query }),
    }).then(json<Resolution>),

  startRun: (query: string, horizon: string, userView: string | null) =>
    fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, horizon, user_view: userView }),
    }).then(json<{ run_id: string; status: string; stream_url: string }>),

  getRun: (runId: string) => fetch(`/api/runs/${runId}`).then(json<RunPayload>),

  history: (limit = 20) =>
    fetch(`/api/runs?limit=${limit}`).then(json<{ runs: HistoryRow[] }>),

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
