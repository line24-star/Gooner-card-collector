/** Typed client for the local server (media, organizing, economy). Throws ApiError on non-2xx. */

import type { Card, FuseOption, PulledCard, SlotsChange } from './types.ts'

export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown) {
    super(messageOf(detail, status))
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

function messageOf(detail: unknown, status: number): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const msgs = detail
      .map((d) => (d && typeof d === 'object' && typeof (d as { msg?: unknown }).msg === 'string' ? (d as { msg: string }).msg : null))
      .filter((m): m is string => m !== null)
    if (msgs.length) return msgs.join('; ')
  }
  if (detail && typeof detail === 'object' && 'message' in detail && typeof (detail as { message: unknown }).message === 'string') {
    return (detail as { message: string }).message
  }
  return `Request failed (${status})`
}

export interface ApiTag { id: number; name: string; parent_id: number | null }
export interface ApiTagCount extends ApiTag { count: number }

export interface ApiCard {
  id: number
  title: string
  kind: 'image' | 'video'
  thumb_url: string | null
  file_url: string
  treatment: string
  owned: Record<string, boolean>
  copies: number
  tags: ApiTag[]
  width: number | null
  height: number | null
  added_at: string | null
  review_available: boolean
  review_reward?: number | null
  upgrade_cost: number | null
  sell_prices: Record<string, number>
  sell_extras?: Record<string, number>
  copies_by_treatment?: Record<string, number>
  value?: number
  print_run?: number
  print_tier?: string
  trend?: number
  /** v0.9.0: the price change over the last update, day, week and month (fractions). `trend` equals `day`. */
  changes?: { update?: number; day?: number; week?: number; month?: number }
  wanted?: boolean
  fuse_from?: string | null
  fuse_copies?: number
  next_tag_reward?: number
  min_pack?: string | null
  title_custom?: boolean
  file_name?: string
  number?: number
  code?: string
  prices?: Record<string, number>
  plain_prices?: Record<string, number>
  sale_quotes?: Record<string, number[]>
  fuse_options?: FuseOption[]
}

export interface ApiWallet {
  coins: number
  packs_opened: number
  streak_days?: number
  last_active_day?: string | null
  next_milestone?: { at: number; reward: { pack: string; count: number }[]; progress?: number; versions_needed?: number; cards?: number } | null
  /** v0.9.0: milestones reached and waiting to be claimed, and their packs in all. */
  milestones_claimable?: { at: number; reward: { pack: string; count: number }[] }[]
  milestones_claimable_packs?: { pack: string; count: number }[]
  completeness?: number
  slots_filled?: number
  slots_total?: number
  tagged_share?: number
  inflation?: number
  pack_prices?: Record<string, number>
  inventory?: Record<string, number>
  pack_max?: Record<string, number>
  pack_exclusives?: Record<string, { cards: number; missing: number }>
  collection_value?: number
  collection_change?: { previous: number; change: number; coins: number }
  market?: { tick: number; index: number; mood: string; change: number; next_tick_at: string; tick_hours: number }
  deal?: { pack: string; discount: number; full_price: number; price: number; left: number; ends_at: string } | null
  rush?: { active: boolean; ends_at: string | null; available: boolean; multiplier: number; minutes: number; available_at?: string | null }
  offer?: { media_id: number; treatment: string; price: number; market_price: number; sell_price: number; expires_at: string; copies?: number; empties_slot?: boolean } | null
  fuse_copies?: number
  cards_total?: number
  cards_owned?: number
  cards_tagged?: number
  copies?: Record<string, number>
  can_check_in?: boolean
  free_pack?: { pack: string | null; available: boolean; claimed_today: boolean; needs_checkin: boolean }
  /** v0.9.0: packs in a row without a Holo or Special. For looks only, it changes no odds. */
  dry_streak?: number
  [k: string]: unknown
}

export interface ScanStatus {
  state: 'idle' | 'scanning'
  done: number
  total: number
  last_report: Record<string, unknown> | null
  /** Bumped whenever a scan or the folder watcher adds, moves, restores or hides media. */
  media_version?: number
}

interface ApiPull {
  media_id: number
  treatment: string
  outcome: string
  near_miss: boolean
  coins: number
  upgraded_to: string | null
  card: ApiCard | null
  is_new: boolean
  value?: number
  fills_slot?: boolean
  already_owned?: boolean
  /** v0.9.0: the face-down glow, a hint that can be wrong; and the name of the treatment shown. */
  hint?: boolean
  treatment_name?: string
}

/**
 * Should the app reload its cards after this scan poll? True when a scan just ended, a scan finished
 * between polls, or media changed between polls (media_version: e.g. the folder watcher ingested files).
 */
export function shouldReload(prev: ScanStatus | null, next: ScanStatus): boolean {
  if (!prev || next.state === 'scanning') return false
  if (prev.state === 'scanning') return true
  if ((prev.media_version ?? 0) !== (next.media_version ?? 0)) return true
  return JSON.stringify(prev.last_report) !== JSON.stringify(next.last_report)
}

export interface OpenPackResult {
  pulls: PulledCard[]
  coins: number
  cost: number
  replayed: boolean
  /** Completeness milestones this pack reached: they wait to be claimed (v0.9.0: nothing is paid during a pack). */
  milestones: { at: number; reward: { pack: string; count: number }[] }[]
  /** Packs in a row without a Holo or Special, after this one. For looks only. */
  dryStreak: number
  free: boolean
  /** Sealed packs left after this one, per pack id. */
  inventory: Record<string, number>
  /** What the pack did to the versions held (the count of pulls with `fillsSlot` is its `delta`). */
  slots?: SlotsChange
}

export interface BuyResult { pack_id: string; bought: number; cost: number; coins: number; inventory: Record<string, number>; replayed: boolean }

/** Buy sealed packs into the inventory. All or nothing: throws ApiError 402 when the coins don't cover them. */
export const buyPacksApi = (packId: string, count: number, idemKey: string, deal = false) =>
  post<BuyResult>(`/api/packs/${encodeURIComponent(packId)}/buy`, { count, deal }, { 'Idempotency-Key': idemKey })

/** Put today's free pack, sealed, into the inventory (409 when there is none to claim). */
export const claimFreePackApi = () => post<{ pack_id: string; inventory: Record<string, number> }>('/api/packs/free/claim')

/** What a scan would do, without starting one (v0.8.0). Completeness as fractions 0..1. */
export interface ScanPreview {
  new_media: number; restored_media: number; missing_media: number; new_slots: number
  slots_filled: number; slots_total: number; slots_filled_after: number; slots_total_after: number
  completeness_now: number; completeness_after: number
  /** v0.9.0: identical copies the scan would skip, the coins it would pay, what it would pay without a limit, and why it pays less. */
  duplicate_media?: number; coins?: number; coins_full?: number; coins_reason?: 'first_import' | 'daily_limit' | null
  ingest_day?: { paid: number; cap: number }
}
export const scanPreviewApi = () => post<ScanPreview>('/api/media/scan?preview=1')

/** Ask the server to scan the media folder now (nothing scans by itself). */
export const startScanApi = () => post<{ started: boolean }>('/api/media/scan')

export function cardFromApi(c: ApiCard): Card {
  return {
    id: c.id,
    title: c.title,
    kind: c.kind,
    imageUrl: c.thumb_url ?? '',
    treatment: c.treatment,
    tags: c.tags.map((t) => t.name),
    copies: c.copies,
    addedAt: c.added_at ?? '',
    thumbUrl: c.thumb_url,
    fileUrl: c.file_url,
    owned: Object.fromEntries(Object.entries(c.owned).map(([k, v]) => [k, v ? 1 : 0])),
    tagIds: c.tags.map((t) => t.id),
    reviewAvailable: c.review_available,
    reviewReward: c.review_reward ?? null,
    upgradeCost: c.upgrade_cost,
    sellPrices: c.sell_prices,
    sellExtras: c.sell_extras ?? {},
    copiesByTreatment: c.copies_by_treatment ?? {},
    value: c.value ?? 0,
    printRun: c.print_run ?? 0,
    printTier: c.print_tier ?? '',
    trend: c.trend ?? 0,
    changes: { update: c.changes?.update ?? 0, day: c.changes?.day ?? c.trend ?? 0, week: c.changes?.week ?? 0, month: c.changes?.month ?? 0 },
    wanted: Boolean(c.wanted),
    number: c.number ?? c.id,
    code: c.code ?? '',
    prices: c.prices ?? {},
    plainPrices: c.plain_prices ?? c.sell_prices,
    saleQuotes: c.sale_quotes ?? {},
    fuseOptions: c.fuse_options ?? [],
    fuseFrom: c.fuse_from ?? null,
    fuseCopies: c.fuse_copies ?? 3,
    nextTagReward: c.next_tag_reward ?? 0,
    minPack: c.min_pack ?? null,
    titleCustom: Boolean(c.title_custom),
    fileName: c.file_name ?? '',
  }
}

/** The backend rejects mutating /api requests without this header (CSRF guard: other sites can't
 * send a custom header without a CORS preflight, which the backend refuses). */
export const CLIENT_HEADER = 'X-MCO-Client'

/** Adds CLIENT_HEADER to any non-GET/HEAD request. Use for EVERY fetch with a method. */
export function withClient(init?: RequestInit): RequestInit | undefined {
  const method = (init?.method ?? 'GET').toUpperCase()
  if (method === 'GET' || method === 'HEAD') return init
  const h = init?.headers
  const headers: Record<string, string> = {}
  if (h instanceof Headers || Array.isArray(h)) new Headers(h).forEach((v, k) => { headers[k] = v })
  else Object.assign(headers, h)
  return { ...init, headers: { ...headers, [CLIENT_HEADER]: '1' } }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, withClient(init))
  if (!r.ok) {
    let detail: unknown = r.statusText
    try { detail = ((await r.json()) as { detail?: unknown }).detail ?? detail } catch { /* not JSON */ }
    throw new ApiError(r.status, detail)
  }
  if (r.status === 204) return undefined as T
  return (await r.json()) as T
}

const post = <T>(url: string, body?: unknown, headers: Record<string, string> = {}) =>
  request<T>(url, {
    method: 'POST',
    headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}), ...headers },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

/** One page of the cards you PULLED. Media that never came out of a pack is not a card yet. */
export async function fetchMediaPage(offset: number, limit: number): Promise<{ total: number; items: ApiCard[] }> {
  return request(`/api/media?filter=pulled&offset=${offset}&limit=${limit}&sort=added`)
}

export async function mediaTotal(): Promise<number> {
  return (await request<{ total: number }>('/api/media?limit=1')).total
}

export async function fetchAllCards(): Promise<Card[]> {
  const PAGE = 500
  // Newest first: a row ingested while paging pushes older rows down, so one can show up on two
  // pages (deduped by id here) but none is skipped.
  const out = new Map<number, Card>()
  for (let offset = 0; ; offset += PAGE) {
    const page = await fetchMediaPage(offset, PAGE)
    for (const item of page.items) if (!out.has(item.id)) out.set(item.id, cardFromApi(item))
    if (!page.items.length || offset + page.items.length >= page.total) break
  }
  return [...out.values()]
}

/** Give a card its own name. An empty name brings the automatic one back. The file is never renamed. */
export async function renameCard(id: number, title: string): Promise<Card> {
  return cardFromApi(await request<ApiCard>(`/api/media/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title: title.trim() || null }),
  }))
}

export async function fetchCard(id: number): Promise<Card> {
  return cardFromApi(await request<ApiCard>(`/api/media/${id}`))
}

export const fetchWallet = () => request<ApiWallet>('/api/economy/wallet')
export const fetchTags = () => request<ApiTagCount[]>('/api/tags')
export const health = () => request<{ profile?: string }>('/api/health')
export const scanStatus = () => request<ScanStatus>('/api/media/scan')

interface ApiOpenResult {
  pulls: ApiPull[]; coins: number; cost: number; replayed: boolean; free?: boolean
  milestones?: { at: number; reward: { pack: string; count: number }[] }[]; dry_streak?: number
  inventory?: Record<string, number>
  slots?: SlotsChange
}

/**
 * Open one sealed pack of this kind from the inventory. `buy`: with none sealed, buy one at today's
 * price and open it; without it an empty inventory throws ApiError 409.
 */
export async function openPackFull(packId: string, idemKey: string, buy = false): Promise<OpenPackResult> {
  return packResult(await post<ApiOpenResult>(
    `/api/packs/${encodeURIComponent(packId)}/open${buy ? '?buy=true' : ''}`, undefined, { 'Idempotency-Key': idemKey }))
}

function packResult(r: ApiOpenResult): OpenPackResult {
  return {
    coins: r.coins,
    cost: r.cost,
    replayed: r.replayed,
    milestones: r.milestones ?? [],
    dryStreak: r.dry_streak ?? 0,
    free: Boolean(r.free),
    inventory: r.inventory ?? {},
    slots: r.slots,
    pulls: r.pulls.filter((p) => p.card).map((p) => {
      const collectionCard = cardFromApi(p.card!)
      // The reveal shows the copy you pulled (or what it upgraded to), not the best copy you own.
      const pulled = (p.outcome === 'upgraded' && p.upgraded_to ? p.upgraded_to : p.treatment) as Card['treatment']
      const outcome = (['new', 'better', 'version', 'upgraded', 'duplicate'].includes(p.outcome) ? p.outcome : 'duplicate') as PulledCard['outcome']
      return { card: { ...collectionCard, treatment: pulled }, collectionCard, isNew: p.is_new, nearMiss: p.near_miss, outcome, coins: p.coins ?? 0, value: p.value ?? 0, fillsSlot: Boolean(p.fills_slot), alreadyOwned: Boolean(p.already_owned), hint: Boolean(p.hint ?? p.fills_slot), treatmentName: p.treatment_name ?? '' }
    }),
  }
}

export async function openPackApi(packId: string, idemKey: string, buy = false): Promise<PulledCard[]> {
  return (await openPackFull(packId, idemKey, buy)).pulls
}

export interface TagResult {
  tag: ApiTag; coins_awarded: number; milestones: unknown[]; coins: number; card: Card
  /** v0.9.0, the tagging pause: this tag was applied during it and paid nothing. */
  paused?: boolean
  /** When the pause ends (ISO), or null; and whether quick keys are locked until then (a typed tag ends it). */
  cooldown_until?: string | null
  must_type?: boolean
}
/** `typed`: the tag was typed into the field (true) or came from a quick key or a click (false). */
export async function addTag(id: number, name: string, parent?: string, typed = false): Promise<TagResult> {
  const r = await post<Omit<TagResult, 'card'> & { card: ApiCard }>(`/api/media/${id}/tags`, { name, parent: parent ?? null, typed })
  return { ...r, card: cardFromApi(r.card) }
}

/** Remove a tag. v0.9.0: the coins that tag had paid on this card are taken back (`refunded`). */
export async function removeTag(id: number, tagId: number): Promise<{ refunded: number; coins: number; card: Card } | undefined> {
  const r = await request<{ refunded: number; coins: number; card: ApiCard } | undefined>(`/api/media/${id}/tags/${tagId}`, { method: 'DELETE' })
  return r && r.card ? { refunded: r.refunded, coins: r.coins, card: cardFromApi(r.card) } : undefined
}

export interface ReviewResult { coins_awarded: number; coins: number; card: Card }
export async function reviewCard(id: number): Promise<ReviewResult> {
  const r = await post<Omit<ReviewResult, 'card'> & { card: ApiCard }>(`/api/media/${id}/review`)
  return { ...r, card: cardFromApi(r.card) }
}

export const bulkTag = (ids: number[], name: string) =>
  post<{ applied: number; coins_awarded: number; coins: number; cooldown_until?: string | null; must_type?: boolean }>('/api/tags/bulk', { media_ids: ids, name })

export const upgradeCard = (id: number) =>
  post<{ success: boolean; cost: number; treatment: string; coins: number; milestones?: unknown[]; slots?: SlotsChange }>(`/api/cards/${id}/upgrade`)

/** Sell `count` copies on the market at today's price. `price` is what the sale paid in total, `coins` the new balance. */
export const sellCard = (id: number, treatment: string, count = 1) =>
  post<{ price: number; sold: number; coins: number; copies_left: number; slots?: SlotsChange }>(`/api/cards/${id}/sell`, { treatment, count })

/** Give up the copies the card needs (its `fuseCopies`: 3, more for a valuable card) of one treatment for one copy a treatment higher. */
export const fuseCard = (id: number, treatment: string) =>
  post<{ treatment: string; from?: string; used: number; coins: number; milestones?: unknown[]; net_versions?: number; slots?: SlotsChange }>(`/api/cards/${id}/fuse`, { treatment })

/** Start today's tag rush (409 when it was already used). */
export const startRushApi = () => post<{ active: boolean; ends_at: string | null }>('/api/economy/rush')
