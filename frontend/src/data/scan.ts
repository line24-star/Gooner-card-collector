/**
 * Scanning the media folder. Nothing scans by itself: the player asks for it (choosing a folder,
 * or a Scan button), and the page only asks the server for progress while a scan is running.
 */

import { scanStatus, startScanApi, type ScanStatus } from './api.ts'
import { initData } from './store.ts'

type Listener = (s: ScanStatus | null) => void
const listeners = new Set<Listener>()
let watching: Promise<ScanStatus | null> | null = null

/** Called with the scan status while one runs, and with null when it has ended. */
export function onScanStatus(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

/**
 * Follow a scan until it ends, then reload the cards and the wallet. `expectStart`: a scan was
 * just requested, so wait a moment for it to begin before deciding nothing is running.
 * Resolves the last status seen (null when the server could not be reached).
 */
export function watchScan(expectStart = false): Promise<ScanStatus | null> {
  if (watching) return watching
  watching = (async () => {
    let last: ScanStatus | null = null
    let seen = false
    let idleChecks = 0
    try {
      for (;;) {
        last = await scanStatus()
        if (last.state === 'scanning') {
          seen = true
          listeners.forEach((fn) => fn(last))
        } else {
          idleChecks++
          if (seen || !expectStart || idleChecks >= 4) break
        }
        await wait(seen ? 1000 : 400)
      }
    } catch {
      last = null
    }
    listeners.forEach((fn) => fn(null))
    if (seen || expectStart) await initData()
    return last
  })().finally(() => { watching = null })
  return watching
}

/** The Scan button: scan the folder now and follow it to the end. */
export async function requestScan(): Promise<ScanStatus | null> {
  await startScanApi()
  return watchScan(true)
}

/** What a scan pays, or would pay (v0.9.0): the report's and the preview's `coins`, `coins_full`, `coins_reason`, `ingest_day`. */
export interface ScanCoins {
  coins?: number; coins_full?: number; coins_reason?: string | null
  ingest_day?: { paid?: number; cap?: number } | null
}

/**
 * The coins of a scan in words, with the reason when it paid less than the full rate:
 * "+45 coins", "0 coins: the first import pays nothing", "+10 coins (daily limit reached: 150 of 150)".
 * '' when the scan found nothing that could pay.
 */
export function scanCoinsText(r: ScanCoins | null | undefined): string {
  const coins = Number(r?.coins ?? 0)
  const full = Number(r?.coins_full ?? 0)
  if (!(coins > 0) && !(full > 0)) return ''
  const got = `${coins > 0 ? '+' : ''}${coins.toLocaleString()} ${coins === 1 ? 'coin' : 'coins'}`
  if (r?.coins_reason === 'first_import') return `${got}: the first import pays nothing`
  if (r?.coins_reason === 'daily_limit') {
    const day = r.ingest_day
    const of = day && Number(day.cap) > 0 ? `: ${Number(day.paid ?? 0).toLocaleString()} of ${Number(day.cap).toLocaleString()}` : ''
    return `${got} (daily limit reached${of})`
  }
  return got
}

/** "12 new cards", "Nothing new", with what else the scan found and what it paid. */
export function scanSummary(st: ScanStatus | null): string {
  const r = st?.last_report as Record<string, number> | null | undefined
  if (!r) return 'The scan could not be followed. Try again.'
  const n = (k: string) => Number(r[k] ?? 0)
  const parts = [n('new') ? `${n('new').toLocaleString()} new ${n('new') === 1 ? 'card' : 'cards'}` : 'Nothing new']
  if (n('restored')) parts.push(`${n('restored')} back`)
  if (n('moved')) parts.push(`${n('moved')} moved`)
  if (n('missing')) parts.push(`${n('missing')} missing`)
  if (n('duplicate')) parts.push(`${n('duplicate')} identical ${n('duplicate') === 1 ? 'copy' : 'copies'} skipped`)
  const coins = scanCoinsText(st?.last_report as ScanCoins | null | undefined)
  if (coins) parts.push(coins)
  return parts.join(' · ')
}
