/** Pure helpers for tagging responses and bulk requests. */

/** Coins the user gained from one tag action: the tag reward. (v0.9.0: milestones pay packs and
 * are claimed, so nothing comes on top.) */
export function gainedCoins(r: { coins_awarded: number; milestones?: unknown[] }): number {
  return r.coins_awarded
}

/** Split ids into slices of at most `size` (size below 1 counts as 1). */
export function chunk<T>(items: T[], size: number): T[][] {
  const n = Math.max(1, Math.floor(size))
  const out: T[][] = []
  for (let i = 0; i < items.length; i += n) out.push(items.slice(i, i + n))
  return out
}

/** "Bulk tags pay a quarter": the rule shown in the bulk-tag bar, from `tagging.bulk_multiplier`. '' when bulk pays the full rate. */
export function bulkRuleText(multiplier: number): string {
  if (!(multiplier < 1)) return ''
  if (multiplier <= 0) return 'Bulk tags pay 1 coin each'
  const named: Record<string, string> = { '0.25': 'a quarter', '0.5': 'half', '0.75': 'three quarters' }
  return `Bulk tags pay ${named[String(multiplier)] ?? `${Math.round(multiplier * 100)}%`}`
}

/** A random pick of `size` items (all of them, shuffled, when there are fewer). `rand` as Math.random. */
export function sample<T>(items: T[], size: number, rand: () => number = Math.random): T[] {
  const pool = items.slice()
  const n = Math.max(0, Math.min(Math.floor(size), pool.length))
  for (let i = 0; i < n; i++) {
    const j = i + Math.floor(Math.min(0.999999, Math.max(0, rand())) * (pool.length - i))
    ;[pool[i], pool[j]] = [pool[j], pool[i]]
  }
  return pool.slice(0, n)
}
