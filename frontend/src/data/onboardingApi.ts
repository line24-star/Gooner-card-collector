/** Getting-started checklist API client. Every mutating call goes through withClient. */

import { ApiError, withClient } from './api.ts'
import { rewardText, type ClaimResult, type PackCount } from './economyApi.ts'

export interface ChecklistStep {
  id: string
  title: string
  kind: string
  target: number
  /** v0.9.0: packs. Empty for a step that pays nothing (it counts as claimed once it is done). */
  reward: PackCount[]
  progress: number
  done: boolean
  claimed: boolean
}

export interface Checklist {
  steps: ChecklistStep[]; complete: boolean; dismissed: boolean
  /** v0.9.0: the tours pay their packs once, when all of them are completed. */
  tours?: { tours: string[]; completed: number; reward: PackCount[]; claimed: boolean }
}

/** "3 of 5 tours taken · 1 Deluxe pack when all are taken"; '' when the tours pay nothing. */
export function toursRewardText(t: Checklist['tours'] | undefined): string {
  if (!t || !t.tours.length) return ''
  const packs = rewardText(t.reward)
  if (!packs) return ''
  const n = t.tours.length
  const done = Math.max(0, Math.min(t.completed, n))
  if (t.claimed) return `All ${n} tours taken · ${packs} paid`
  return `${done} of ${n} tours taken · ${packs} when all are taken`
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, withClient(init))
  if (!r.ok) {
    let detail: unknown = r.statusText
    try { detail = ((await r.json()) as { detail?: unknown }).detail ?? detail } catch { /* not JSON */ }
    throw new ApiError(r.status, detail)
  }
  return (await r.json()) as T
}

export const fetchOnboarding = () => call<Checklist>('/api/onboarding')
export const claimOnboarding = (id: string) =>
  call<ClaimResult>(`/api/onboarding/${encodeURIComponent(id)}/claim`, { method: 'POST' })
export const dismissOnboarding = () => call<{ dismissed: boolean }>('/api/onboarding/dismiss', { method: 'POST' })
/** Bring the hidden checklist back. */
export const restoreOnboarding = () => call<{ dismissed: boolean }>('/api/onboarding/restore', { method: 'POST' })
