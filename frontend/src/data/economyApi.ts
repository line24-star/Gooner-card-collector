/** Client for the daily check-in, weekly goals, category sets and pull history. */

import { ApiError, cardFromApi, withClient, type ApiCard } from './api.ts'
import type { Card } from './types.ts'

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, withClient(init))
  if (!r.ok) {
    let detail: unknown = r.statusText
    try { detail = ((await r.json()) as { detail?: unknown }).detail ?? detail } catch { /* not JSON */ }
    throw new ApiError(r.status, detail)
  }
  return (await r.json()) as T
}

const post = <T>(url: string, body?: unknown) =>
  call<T>(url, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

// ---- rewards are sealed packs (v0.9.0) ----

/** A reward: `count` sealed packs of one kind. Every reward is a list of these (empty = no reward). */
export interface PackCount { pack: string; count: number }
/** What every claim answers: the packs given, the sealed packs after it, and the (unchanged) coins. */
export interface ClaimResult { packs: PackCount[]; inventory: Record<string, number>; coins: number }
/** A milestone that is reached and waits to be claimed. */
export interface MilestoneReward { at: number; reward: PackCount[] }

const PACK_NAMES: Record<string, string> = { standard: 'Standard', deluxe: 'Deluxe', premium: 'Premium' }

/** "1 Standard pack", "2 Deluxe packs + 1 Premium pack"; empty for no reward. */
export function rewardText(packs: PackCount[] | undefined | null): string {
  return (packs ?? []).filter((p) => p.count > 0)
    .map((p) => `${p.count} ${PACK_NAMES[p.pack] ?? p.pack} ${p.count === 1 ? 'pack' : 'packs'}`).join(' + ')
}

/** How many packs a reward holds in all. */
export const rewardCount = (packs: PackCount[] | undefined | null): number =>
  (packs ?? []).reduce((n, p) => n + p.count, 0)

// ---- daily check-in ----

/** v0.9.0: the check-in pays no coins; it unlocks the free daily pack. */
export interface CheckinResult { checked_in: boolean; streak_days: number; milestones: MilestoneReward[]; coins: number }
export const checkIn = () => post<CheckinResult>('/api/economy/checkin')

// ---- weekly goals ----

export interface Goal { id: string; title: string; kind: string; target: number; reward: PackCount[]; progress: number; claimed: boolean }
export interface Goals {
  week: string
  locked: boolean
  goals: Goal[]
  /** While locked: packs needed before weekly goals appear, and how many are opened so far. */
  unlock_after_packs?: number
  packs_opened?: number
}
export const fetchGoals = () => call<Goals>('/api/economy/goals')
export const claimGoal = (id: string) => post<ClaimResult>(`/api/economy/goals/${encodeURIComponent(id)}/claim`)

// ---- the chase: daily goals, weekly goals, collector level, and the one next goal (v0.6.0) ----

export interface GoalPeriod {
  key: string
  locked: boolean
  unlock_after_packs?: number
  packs_opened?: number
  /** When the goals are replaced by new ones. */
  ends_at?: string
  goals: Goal[]
}
export interface Mastery {
  level: number
  points: number
  /** Points at which the current level was reached, and at which the next one is. */
  level_at: number
  next_at: number
  next_reward: PackCount[]
  claimable: { level: number; reward: PackCount[] }[]
  /** The packs of every level that waits to be claimed, in all. */
  claimable_packs: PackCount[]
  cards?: Record<string, number>
  fuses?: number
}
export interface NextGoal { kind: string; title: string; progress: number; target: number; reward: PackCount[]; href?: string; /** Reached: only the claim is left. */ claimable?: boolean }
/** v0.9.0: milestones are claimed. `claimable`: reached and waiting; `next`: the one still to reach. */
export interface MilestonesStatus { claimable: MilestoneReward[]; claimable_packs: PackCount[]; next: { at: number; reward: PackCount[] } | null }
export interface Chase { daily: GoalPeriod; weekly: GoalPeriod; mastery: Mastery; milestones?: MilestonesStatus; next: NextGoal | null }

export const fetchChase = () => call<Chase>('/api/economy/chase')
export const claimChase = (scope: 'daily' | 'weekly', id: string) =>
  post<ClaimResult>('/api/economy/chase/claim', { scope, id })
export const claimMastery = () => post<ClaimResult & { levels: number[] }>('/api/economy/mastery/claim')
/** Take the packs of every milestone that is reached and not yet claimed (409 when none waits). */
export const claimMilestones = () => post<ClaimResult & { milestones: number[] }>('/api/economy/milestones/claim')

// ---- category sets ----

export interface SetTier { treatment: string; progress: number; complete: boolean; reward: PackCount[]; claimed: boolean }
export interface CardSet { tag_id: number; name: string; size: number; tiers: SetTier[] }
export const fetchSets = () => call<CardSet[]>('/api/economy/sets')
export const claimSet = (tagId: number, treatment: string) =>
  post<ClaimResult>(`/api/economy/sets/${tagId}/claim`, { treatment })

/** The set tier the player is closest to finishing (unclaimed, most complete first), or null. */
export function closestTier(sets: CardSet[]): { set: CardSet; tier: SetTier } | null {
  let best: { set: CardSet; tier: SetTier } | null = null
  for (const set of sets) {
    for (const tier of set.tiers) {
      if (tier.claimed || !tier.reward.length) continue
      const share = tier.progress / set.size
      if (!best || share > best.tier.progress / best.set.size) best = { set, tier }
    }
  }
  return best
}

// ---- pull history ----

export interface RecentPack {
  id: number
  pack: string | null
  at: string
  cost: number
  free: boolean
  cards: number
  best: string | null
  near_misses: number
  new: number
  better: number
  upgraded: number
  /** Pulls of a treatment the player held no copy of, below the best one (v0.7.1). */
  version?: number
  duplicate: number
}
export interface NearMiss { media_id: number; treatment: string; at: string; card: Card }
export interface History {
  packs_today: number
  packs_this_week: number
  near_misses_this_week: number
  recent_packs: RecentPack[]
  near_misses: NearMiss[]
}

export async function fetchHistory(): Promise<History> {
  const h = await call<Omit<History, 'near_misses'> & { near_misses: (Omit<NearMiss, 'card'> & { card: ApiCard })[] }>('/api/economy/history')
  return { ...h, near_misses: h.near_misses.map((m) => ({ ...m, card: { ...cardFromApi(m.card), treatment: m.treatment } })) }
}
