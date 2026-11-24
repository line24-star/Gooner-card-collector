/** Countdowns: "2 h 13 min" style text for an ISO time, kept live by one timer per page. */

/** Time left until `untilIso`: "2 h 13 min", "4 min 05 s" under an hour, "now" when past or invalid. */
export function countdownText(untilIso: string | null, now = Date.now()): string {
  const until = untilIso ? Date.parse(untilIso) : NaN
  if (!Number.isFinite(until)) return 'now'
  const secs = Math.floor((until - now) / 1000)
  if (secs <= 0) return 'now'
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = secs % 60
  if (h > 0) return `${h} h ${String(m).padStart(2, '0')} min`
  if (m > 0) return `${m} min ${String(s).padStart(2, '0')} s`
  return `${s} s`
}

/**
 * Every second, set the text of each `[data-countdown="<iso>"]` inside root. `onZero` is called
 * once per element value that reaches zero. Elements are queried on each tick, so they may come
 * and go. Returns a stop function.
 */
export function startCountdowns(root: HTMLElement, onZero?: () => void): () => void {
  const fired = new Set<string>()
  const tick = () => {
    const now = Date.now()
    let zero = false
    root.querySelectorAll<HTMLElement>('[data-countdown]').forEach((el) => {
      const iso = el.dataset.countdown ?? ''
      const text = countdownText(iso, now)
      if (el.textContent !== text) el.textContent = text
      if (text === 'now' && !fired.has(iso)) {
        fired.add(iso)
        zero = true
      }
    })
    if (zero) onZero?.()
  }
  tick()
  const id = window.setInterval(tick, 1000)
  return () => window.clearInterval(id)
}

/**
 * The times of day the prices change, in the viewer's own clock: "01:00, 07:00, 13:00 and 19:00".
 * `nextIso` is the next update, `hours` the time between two updates. '' when unknown.
 */
export function tickClockTimes(nextIso: string | null, hours: number, format: (d: Date) => string = hhmm): string {
  const next = nextIso ? Date.parse(nextIso) : NaN
  if (!Number.isFinite(next) || !(hours > 0) || hours > 24) return ''
  const n = Math.max(1, Math.round(24 / hours))
  const times = [...new Set(Array.from({ length: n }, (_, i) => format(new Date(next + i * hours * 3600_000))))].sort()
  return times.length > 1 ? `${times.slice(0, -1).join(', ')} and ${times[times.length - 1]}` : times[0]
}
const hhmm = (d: Date) => `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
