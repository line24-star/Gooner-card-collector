/**
 * The tagging pause, as the player meets it (v0.9.0, test round 6 point 21).
 *
 * The server starts a short pause after a run of very fast quick-key tags: a tag sent with a quick
 * key during the pause is put on the card but pays nothing, and a typed tag ends the pause and is
 * paid. Every tag reply says where the pause stands (`cooldown_until`, `must_type`, `paused`).
 * These are the pure rules the pages share: what the state is, how long is left, what to say.
 * The wording never accuses: it says what to do next.
 */

export interface TagPause {
  /** When the pause ends (ms since the epoch), or null when there is none. */
  until: number | null
  /** Quick keys and clicks are locked until then; a typed tag ends the pause at once. */
  mustType: boolean
}

export const NO_PAUSE: TagPause = { until: null, mustType: false }

/** The pause as a tag reply states it. A reply without the fields (an older server) is no pause. */
export function pauseFrom(reply: { cooldown_until?: string | null; must_type?: boolean }): TagPause {
  const until = reply.cooldown_until ? Date.parse(reply.cooldown_until) : NaN
  if (!Number.isFinite(until)) return NO_PAUSE
  return { until, mustType: Boolean(reply.must_type) }
}

/** Whole seconds left, rounded up ("wait 4 s" while 3.2 s are left); 0 when the pause is over. */
export function pauseSeconds(p: TagPause, now = Date.now()): number {
  if (p.until === null) return 0
  return Math.max(0, Math.ceil((p.until - now) / 1000))
}

/** Quick keys and clicks are locked right now. */
export function quickLocked(p: TagPause, now = Date.now()): boolean {
  return p.mustType && pauseSeconds(p, now) > 0
}

/**
 * The line shown while the pause runs, or '' when it is over.
 * `unpaid`: the tag just sent landed in the pause, so it was added and paid nothing.
 */
export function pauseText(p: TagPause, now = Date.now(), unpaid = false): string {
  const s = pauseSeconds(p, now)
  const lost = unpaid ? 'That tag was added, but it paid nothing: it was sent during the pause. ' : ''
  if (!p.mustType || s < 1) return lost.trim()
  return `${lost}Slow down: type the next tag, or wait ${s} s.`
}

/** What a removed tag says: `Removed "beach" · −10 coins`, with a real minus sign; no amount when it had paid nothing. */
export function removedTagText(name: string, refunded: number): string {
  if (!(refunded > 0)) return `Removed "${name}". It had paid nothing.`
  return `Removed "${name}" · −${refunded.toLocaleString()} ${refunded === 1 ? 'coin' : 'coins'}`
}
