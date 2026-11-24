/** Client for /api/account (the Player page) and the coin history. */

import { ApiError, withClient } from './api.ts'

export interface Account {
  name: string
  since: string | null
  coins: number
  packs_opened: number
  streak_days: number
  earned: number
  spent: number
  by_reason: { reason: string; total: number; count: number }[]
  cards_total: number
  cards_owned: number
  media_folder: string
  log_folder: string
}

export interface Tx {
  id: number
  amount: number
  reason: string
  ref: string
  balance_after: number
  created_at: string
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, withClient(init))
  if (!r.ok) {
    let detail: unknown = r.statusText
    try { detail = ((await r.json()) as { detail?: unknown }).detail ?? detail } catch { /* not JSON */ }
    throw new ApiError(r.status, detail)
  }
  if (r.status === 204) return undefined as T
  return (await r.json()) as T
}

export const fetchAccount = () => call<Account>('/api/account')

export const renameAccount = (name: string) =>
  call<Account>('/api/account', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })

export const openLogFolder = () => call<void>('/api/account/logs/open', { method: 'POST' })

/** Wipe the player's progress (coins, packs, cards, tags, streak, checklist, tours). The library stays. */
export const resetAccount = () =>
  call<Account>('/api/account/reset', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: 'RESET' }) })

export const fetchTransactions = (limit = 30) => call<Tx[]>(`/api/economy/transactions?limit=${limit}`)

const REASONS: Record<string, string> = {
  starting_grant: 'Welcome coins',
  ingest: 'New photo or video',
  tag: 'Tagging',
  review: 'Reviewing a rarer copy',
  milestone: 'Collection milestone',
  category: 'Category reward',
  streak: 'Daily check-in',
  pack: 'Pack bought',
  duplicate: 'Duplicate from a pack',
  sell: 'Card sold on the market',
  upgrade_attempt: 'Upgrade attempt',
  onboarding: 'Getting-started step',
  tour: 'Tour reward',
  tag_refund: 'Tag removed',
  tag_removed: 'Tag (removed later)',
  goal: 'Weekly goal',
  set: 'Set reward',
  market_buy: 'Card bought on the market',
  daily_goal: 'Daily goal',
  mastery: 'Collector level reached',
  offer: 'Sold to a collector',
  free_pack: 'Daily free pack',
}

/** Plain-language name for a ledger reason (unknown reasons are shown as written, spaced). */
export function reasonLabel(reason: string): string {
  return REASONS[reason] ?? reason.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase())
}
