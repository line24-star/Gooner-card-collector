/**
 * Sends the browser's own errors to the server log (POST /api/log), so a button that "does nothing"
 * leaves a trace in data/logs/server.log. Never throws and never reports its own failures.
 */

import { withClient } from './api.ts'

let sent = 0
const MAX_PER_PAGE = 30 // a render loop must not flood the log

export function reportError(message: string, level: 'error' | 'warn' | 'info' = 'error'): void {
  if (sent >= MAX_PER_PAGE) return
  sent++
  try {
    void fetch('/api/log', withClient({
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ level, message: message.slice(0, 4000), where: location.hash.slice(0, 300) }),
    })).catch(() => {})
  } catch { /* logging must never break the app */ }
}

export function describeError(e: unknown): string {
  if (e instanceof Error) return `${e.name}: ${e.message}${e.stack ? `\n${e.stack}` : ''}`
  try { return typeof e === 'string' ? e : JSON.stringify(e) } catch { return String(e) }
}

export function installErrorReporting(win: Window = window): void {
  win.addEventListener('error', (e) => reportError(describeError(e.error ?? e.message)))
  win.addEventListener('unhandledrejection', (e) => reportError(`Unhandled promise rejection: ${describeError(e.reason)}`))
}
