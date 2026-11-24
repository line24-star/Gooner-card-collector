/**
 * Server lifecycle helpers.
 *
 * - startPresence(): holds a WebSocket open so the backend knows a tab is alive.
 *   When every tab closes, the backend shuts itself down after a short grace period.
 * - stopServer(): asks the backend to shut down now (the Stop button).
 */

import { withClient } from './data/api.ts'

export type ServerState = 'connecting' | 'online' | 'offline' | 'stopped'

type Listener = (state: ServerState) => void

const PING_MS = 10_000
const RETRY_MS = 2_000
const GIVE_UP_AFTER_MS = 20_000 // longer than the backend's grace period

let state: ServerState = 'connecting'
const listeners = new Set<Listener>()

function setState(next: ServerState) {
  if (state === next) return
  state = next
  listeners.forEach((fn) => fn(state))
}

export function onServerState(fn: Listener): () => void {
  listeners.add(fn)
  fn(state)
  return () => listeners.delete(fn)
}

export function startPresence(): void {
  let lostAt: number | null = null
  let pingTimer: number | undefined

  const connect = () => {
    if (state === 'stopped') return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${proto}://${location.host}/api/ws/presence`)

    ws.onopen = () => {
      lostAt = null
      setState('online')
      pingTimer = window.setInterval(() => ws.send('ping'), PING_MS)
    }
    ws.onclose = () => {
      window.clearInterval(pingTimer)
      if (state === 'stopped') return
      lostAt ??= Date.now()
      if (Date.now() - lostAt > GIVE_UP_AFTER_MS) {
        setState('stopped')
        return
      }
      setState('offline')
      window.setTimeout(connect, RETRY_MS)
    }
  }

  connect()
}

export async function stopServer(): Promise<void> {
  setState('stopped') // stop reconnect attempts first
  try {
    await fetch('/api/shutdown', withClient({ method: 'POST', headers: { 'X-MCO-Action': 'shutdown' } }))
  } catch {
    // Server may close the connection while answering; that's fine.
  }
}
