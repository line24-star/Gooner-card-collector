/**
 * Tiny observable app state. Everything comes from the server: cards, wallet, prices and packs.
 * Two modes: `empty` (no media imported yet: the app shows only the setup screen) and `live`.
 * The balance is never computed locally.
 */

import { FALLBACK_TREATMENTS, PACK_TYPES } from './mock.ts'
import type { Card, PackType, PulledCard, SlotsChange, TreatmentInfo, Wallet } from './types.ts'
import { buyPacksApi, claimFreePackApi, fetchAllCards, fetchWallet, mediaTotal, openPackFull, type ApiWallet, type BuyResult } from './api.ts'

export interface AppState {
  cards: Card[]
  treatments: TreatmentInfo[]
  packTypes: PackType[]
  wallet: Wallet
  mode: 'empty' | 'live' // empty = nothing imported yet (setup screen only)
  /** Upgrade attempts: `enabled` false (the live config since v0.7.1) means there are none. */
  upgrades: { enabled?: boolean; chance: number; cost: Record<string, number> }
  limits: { bulk_max_items: number }
  /** v0.9.0: the share of the rate a bulk tag pays, and how long the tagging pause lasts (seconds). */
  tagging: { bulkMultiplier: number; cooldownSeconds: number }
  /** Welcome coins a new player starts with (from the server config; null until it loads). */
  startingCoins: number | null
  /** The market's rules from the server config: only cards worth `wantedMinValue` or more are traded. */
  market: { listingsPerTreatment: number; sellRate: number; buyMarkup: number; wantedBonus: number; wantedMinValue: number }
}

type Listener = (s: AppState) => void

const state: AppState = {
  cards: [],
  treatments: FALLBACK_TREATMENTS,
  packTypes: PACK_TYPES,
  wallet: {
    coins: 0, packsOpened: 0, packPrices: {}, streakDays: 0, lastActiveDay: null, completeness: 0, slotsFilled: 0, slotsTotal: 0, nextMilestone: null,
    inventory: {}, cardsTotal: 0, cardsOwned: 0, cardsTagged: 0, copies: {},
    packMax: {}, packExclusives: {}, collectionValue: 0, collectionChange: { change: 0, coins: 0 }, fuseCopies: 3, deal: null, offer: null,
    market: { tick: 0, index: 1, mood: 'flat', change: 0, nextTickAt: null, tickHours: 6 },
    rush: { active: false, endsAt: null, available: false, multiplier: 1, minutes: 0, availableAt: null },
    canCheckIn: false,
    freePack: { pack: null, available: false, claimedToday: false, needsCheckin: false },
    dryStreak: 0, milestonesClaimable: [], milestonesClaimablePacks: [],
  },
  mode: 'empty',
  upgrades: { chance: 0, cost: {} },
  limits: { bulk_max_items: 200 },
  tagging: { bulkMultiplier: 0.25, cooldownSeconds: 4 },
  startingCoins: null,
  market: { listingsPerTreatment: 10, sellRate: 0.85, buyMarkup: 1.15, wantedBonus: 1.3, wantedMinValue: 20 },
}
const listeners = new Set<Listener>()

export function getState(): AppState {
  return state
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function emit() {
  listeners.forEach((fn) => fn(state))
}

const libraryKey = (s: AppState) => `${s.mode}:${s.cards.length}`

/**
 * Call fn when the library changes shape (empty <-> live, or a different number of cards), e.g.
 * after the folder watcher imported files. Ignores coin and single-card updates, so pages that
 * remount on it don't re-render on every tag. Returns an unsubscribe function.
 */
export function onLibraryChange(fn: () => void): () => void {
  let key = libraryKey(state)
  return subscribe((s) => {
    const next = libraryKey(s)
    if (next === key) return
    key = next
    fn()
  })
}

interface ServerEconomyConfig {
  treatments: TreatmentInfo[]
  upgrades?: AppState['upgrades']
  limits?: AppState['limits']
  tagging?: { bulk_multiplier?: number; cooldown_seconds?: number }
  starting_coins?: number
  market?: { listings_per_treatment?: number; sell_rate?: number; buy_markup?: number; wanted_bonus?: number; wanted_min_value?: number }
  packs: { id: string; name: string; blurb: string; size: number; base_cost: number; hue: number; odds?: Record<string, number> }[]
}

/**
 * Load server-authoritative config (config/economy.toml via /api/economy/config):
 * treatments, odds and pack types. Falls back to bundled defaults if the API is down.
 */
export async function loadConfig(): Promise<void> {
  try {
    const r = await fetch('/api/economy/config')
    if (!r.ok) return
    const cfg = (await r.json()) as ServerEconomyConfig
    state.treatments = cfg.treatments
    if (cfg.upgrades) state.upgrades = cfg.upgrades
    if (cfg.limits) state.limits = cfg.limits
    if (cfg.tagging) {
      state.tagging = {
        bulkMultiplier: cfg.tagging.bulk_multiplier ?? state.tagging.bulkMultiplier,
        cooldownSeconds: cfg.tagging.cooldown_seconds ?? state.tagging.cooldownSeconds,
      }
    }
    if (typeof cfg.starting_coins === 'number') state.startingCoins = cfg.starting_coins
    if (cfg.market) {
      const m = cfg.market
      state.market = {
        listingsPerTreatment: m.listings_per_treatment ?? state.market.listingsPerTreatment,
        sellRate: m.sell_rate ?? state.market.sellRate,
        buyMarkup: m.buy_markup ?? state.market.buyMarkup,
        wantedBonus: m.wanted_bonus ?? state.market.wantedBonus,
        wantedMinValue: m.wanted_min_value ?? state.market.wantedMinValue,
      }
    }
    state.packTypes = cfg.packs.map((p) => ({
      id: p.id, name: p.name, blurb: p.blurb, cost: p.base_cost, size: p.size, hue: p.hue, odds: p.odds ?? {},
    }))
    emit()
  } catch {
    /* keep fallback */
  }
}

// ---- Derived values ----

/**
 * Where the collection stands, from the server's numbers (the client only holds the cards you
 * pulled, so it can't count the rest):
 *  - total: every photo and video in the folder: each one is a card you can pull
 *  - owned: cards pulled at least once; `pulled` is that share of the total
 *  - tagged: owned cards with at least one tag; `ratio` is that share of the owned cards
 *  - complete: the share of versions you hold now. Every card in the folder has three (Foil,
 *    Holo, Special); `versions` of `versionsTotal` are held. Normal copies and tags don't count.
 */
export function completeness() {
  const w = state.wallet
  return {
    total: w.cardsTotal,
    owned: w.cardsOwned,
    tagged: w.cardsTagged,
    pulled: w.cardsTotal ? w.cardsOwned / w.cardsTotal : 0,
    ratio: w.cardsOwned ? w.cardsTagged / w.cardsOwned : 0,
    complete: w.completeness,
    versions: w.slotsFilled,
    versionsTotal: w.slotsTotal,
  }
}

/** A share as text: one decimal while it is small ("0.4%"), whole numbers from 10% on. */
export function pctText(share: number): string {
  const p = share * 100
  return `${p > 0 && p < 9.95 ? p.toFixed(1) : Math.round(p)}%`
}

/** "1,240 of 3,000 versions": the Foil, Holo and Special versions held, of three per card. */
export function versionsText(c: { versions: number; versionsTotal: number }): string {
  return `${c.versions.toLocaleString()} of ${c.versionsTotal.toLocaleString()} versions`
}

/** The three versions every card has, lowest first. Normal is the base and is not a version. */
export const VERSION_IDS = ['foil', 'holo', 'special'] as const

/** Which of Foil, Holo and Special this card lacks (no copy held), lowest first. */
export function missingVersions(card: Pick<Card, 'copiesByTreatment'>): string[] {
  return VERSION_IDS.filter((t) => (card.copiesByTreatment[t] ?? 0) < 1)
}

/** How many pulled cards lack at least one version. */
export function missingCount(cards: Pick<Card, 'copies' | 'copiesByTreatment'>[]): number {
  return cards.filter((c) => c.copies > 0 && missingVersions(c).length > 0).length
}

/** A change in versions as text: "+2", "−1" (a real minus sign), or '' when nothing changed. */
export function versionDeltaText(delta: number): string {
  if (!delta) return ''
  return `${delta > 0 ? '+' : '−'}${Math.abs(delta).toLocaleString()}`
}

// ---- Version changes (v0.8.0) ----

type SlotsListener = (change: SlotsChange) => void
const slotListeners = new Set<SlotsListener>()

/** Called once for every action that filled or emptied a version slot, or changed the total (a scan). */
export function onSlotsChange(fn: SlotsListener): () => void {
  slotListeners.add(fn)
  return () => slotListeners.delete(fn)
}

/**
 * Take the `slots` object of an action's reply: the wallet's version numbers are set at once (no
 * wallet reload needed), every subscriber is told, and the listeners of `onSlotsChange` get the
 * change itself (the delta, and which slots filled or emptied) for the pill and for effects.
 */
export function applySlots(change: SlotsChange | null | undefined): void {
  if (!change) return
  state.wallet.slotsFilled = Math.max(0, change.filled - heldVersions)
  state.wallet.slotsTotal = change.total
  state.wallet.completeness = change.total > 0 ? state.wallet.slotsFilled / change.total : 0
  emit()
  const moved = change.delta !== 0 || (change.total_delta ?? 0) !== 0
    || (change.filled_now?.length ?? 0) > 0 || (change.emptied?.length ?? 0) > 0
  if (moved) slotListeners.forEach((fn) => fn(change))
}

// ---- Versions of a pack whose cards are still face down ----

/**
 * Versions the server already counts but the player has not seen yet: the pack is open, the card
 * that fills the slot is still face down. They are kept out of the wallet's version numbers (so
 * the pill does not give them away, whatever reloads the wallet meanwhile) until `revealVersion`
 * shows them one by one, or `settleVersions` shows what is left.
 */
let heldVersions = 0

/** Take the server's version numbers down by what is still face down. */
function holdBack(w: Wallet): Wallet {
  if (heldVersions > 0) {
    w.slotsFilled = Math.max(0, w.slotsFilled - heldVersions)
    w.completeness = w.slotsTotal > 0 ? w.slotsFilled / w.slotsTotal : 0
  }
  return w
}

/** A face-down card that fills a slot was turned: the pill goes up by one, now. */
export function revealVersion(mediaId: number, treatment: string): void {
  if (heldVersions < 1) return
  heldVersions -= 1
  const w = state.wallet
  applySlots({
    filled: w.slotsFilled + 1 + heldVersions, total: w.slotsTotal, delta: 1,
    filled_now: [{ media_id: mediaId, treatment }], emptied: [], total_delta: 0,
  })
}

/** Show every version still held back at once (the overlay closed with cards face down). */
export function settleVersions(): void {
  if (heldVersions < 1) return
  const n = heldVersions
  heldVersions = 0
  const w = state.wallet
  applySlots({ filled: w.slotsFilled + n, total: w.slotsTotal, delta: n, filled_now: [], emptied: [], total_delta: 0 })
}

/** Sealed packs owned, all kinds together. */
export function sealedTotal(): number {
  return Object.values(state.wallet.inventory).reduce((n, x) => n + x, 0)
}

/** What the server charges for this pack right now (prices rise as you play). */
export function packCost(pack: PackType): number {
  return state.wallet.packPrices[pack.id] ?? pack.cost
}

/** How many of this pack the balance buys at today's price (a lower bound: each pack raises the next price). */
export function affordable(pack: PackType): number {
  // The server counts the price rising with every pack; its number wins when it is known.
  const known = state.wallet.packMax[pack.id]
  if (known !== undefined) return known
  return Math.floor(state.wallet.coins / packCost(pack))
}

// ---- Live data ----

function walletFromApi(w: ApiWallet): Wallet {
  return {
    coins: w.coins,
    packsOpened: w.packs_opened,
    packPrices: w.pack_prices ?? {},
    streakDays: w.streak_days ?? 0,
    lastActiveDay: w.last_active_day ?? null,
    completeness: w.completeness ?? 0,
    slotsFilled: w.slots_filled ?? 0,
    slotsTotal: w.slots_total ?? 0,
    nextMilestone: w.next_milestone ? {
      at: w.next_milestone.at, reward: w.next_milestone.reward, progress: w.next_milestone.progress,
      versionsNeeded: w.next_milestone.versions_needed, cards: w.next_milestone.cards,
    } : null,
    inventory: w.inventory ?? {},
    packMax: w.pack_max ?? {},
    packExclusives: w.pack_exclusives ?? {},
    collectionValue: w.collection_value ?? 0,
    collectionChange: { change: w.collection_change?.change ?? 0, coins: w.collection_change?.coins ?? 0 },
    fuseCopies: w.fuse_copies ?? 3,
    market: {
      tick: w.market?.tick ?? 0, index: w.market?.index ?? 1, mood: w.market?.mood ?? 'flat',
      change: w.market?.change ?? 0, nextTickAt: w.market?.next_tick_at ?? null, tickHours: w.market?.tick_hours ?? 6,
    },
    deal: w.deal ? {
      pack: w.deal.pack, discount: w.deal.discount, fullPrice: w.deal.full_price, price: w.deal.price,
      left: w.deal.left, endsAt: w.deal.ends_at,
    } : null,
    rush: {
      active: w.rush?.active ?? false, endsAt: w.rush?.ends_at ?? null, available: w.rush?.available ?? false,
      multiplier: w.rush?.multiplier ?? 1, minutes: w.rush?.minutes ?? 0, availableAt: w.rush?.available_at ?? null,
    },
    offer: w.offer ? {
      mediaId: w.offer.media_id, treatment: w.offer.treatment, price: w.offer.price,
      marketPrice: w.offer.market_price, sellPrice: w.offer.sell_price, expiresAt: w.offer.expires_at,
      copies: w.offer.copies ?? 0, emptiesSlot: Boolean(w.offer.empties_slot),
    } : null,
    cardsTotal: w.cards_total ?? 0,
    cardsOwned: w.cards_owned ?? 0,
    cardsTagged: w.cards_tagged ?? 0,
    copies: w.copies ?? {},
    canCheckIn: w.can_check_in ?? false,
    freePack: {
      pack: w.free_pack?.pack ?? null,
      available: w.free_pack?.available ?? false,
      claimedToday: w.free_pack?.claimed_today ?? false,
      needsCheckin: w.free_pack?.needs_checkin ?? false,
    },
    dryStreak: w.dry_streak ?? 0,
    milestonesClaimable: w.milestones_claimable ?? [],
    milestonesClaimablePacks: w.milestones_claimable_packs ?? [],
  }
}

/**
 * Load the wallet and the cards. `live` when the media folder has files, else `empty`.
 * A failed request keeps the current state untouched.
 */
export async function initData(): Promise<void> {
  try {
    const [total, wallet] = await Promise.all([mediaTotal(), fetchWallet()])
    state.wallet = holdBack(walletFromApi(wallet))
    if (total > 0) {
      state.cards = await fetchAllCards()
      state.mode = 'live'
    } else {
      state.cards = []
      state.mode = 'empty'
    }
    emit()
    watchPriceUpdate()
  } catch (e) {
    console.warn('initData failed; keeping current data', e)
  }
}

/**
 * Fetch the wallet. When it brings a new price update (`market.tick` changed), the cards are
 * fetched again before anyone is told: every card's value, sell prices, wanted mark, fuse count
 * and upgrade cost belong to one price update, so no page ever shows cards of the old one next to
 * a wallet of the new one. This is the one place cards follow the market; pages only subscribe.
 */
export async function refreshWallet(): Promise<void> {
  const before = state.wallet.market.tick
  const wallet = holdBack(walletFromApi(await fetchWallet()))
  if (state.mode === 'live' && before !== 0 && wallet.market.tick !== before) {
    // Two clocks can ask at the same moment (the Messages button and the price update clock): one card fetch serves both.
    if (!cardsFlight || cardsFlight.tick !== wallet.market.tick) cardsFlight = { tick: wallet.market.tick, cards: fetchAllCards() }
    const flight = cardsFlight
    try { state.cards = await flight.cards } catch (e) { console.warn('could not reload the cards after a price update', e) }
    if (cardsFlight === flight) cardsFlight = null
  }
  state.wallet = wallet
  emit()
  watchPriceUpdate()
}

let cardsFlight: { tick: number; cards: Promise<Card[]> } | null = null

// ---- The price update clock ----

let priceTimer: ReturnType<typeof setTimeout> | undefined
/** Not before this long after the announced moment, and how soon to ask again when the server is not there yet. */
const PRICE_UPDATE_GRACE_MS = 1500
const PRICE_UPDATE_RETRY_MS = 15_000
const MAX_TIMEOUT_MS = 2 ** 31 - 1

/**
 * One timer for the whole app, set to the wallet's next price update. When it fires the wallet is
 * fetched (and with it the cards, see refreshWallet), whatever page is open: Collection, Stats and
 * the Player page have no clock of their own. Set again after every wallet fetch.
 */
function watchPriceUpdate(): void {
  if (priceTimer !== undefined) clearTimeout(priceTimer)
  priceTimer = undefined
  const next = state.wallet.market.nextTickAt ? Date.parse(state.wallet.market.nextTickAt) : NaN
  if (state.mode !== 'live' || !Number.isFinite(next)) return
  const wait = next - Date.now()
  // Already past: the server has not moved yet (or the clocks differ a little). Ask again soon.
  const delay = wait > 0 ? Math.min(wait + PRICE_UPDATE_GRACE_MS, MAX_TIMEOUT_MS) : PRICE_UPDATE_RETRY_MS
  priceTimer = setTimeout(() => {
    priceTimer = undefined
    refreshWallet().catch(() => watchPriceUpdate())
  }, delay)
}

/** Stop the price update clock (tests). */
export function stopPriceWatch(): void {
  if (priceTimer !== undefined) clearTimeout(priceTimer)
  priceTimer = undefined
}

/** Swap in a fresh server copy of one card (after tagging, review, upgrade...). */
function put(card: Card): void {
  const i = state.cards.findIndex((c) => c.id === card.id)
  if (i >= 0) state.cards[i] = card
  else state.cards.push(card)
}

export function replaceCard(card: Card): void {
  put(card)
  emit()
}

/** A card left the collection (its last copy was sold or fused away). */
export function removeCard(id: number): void {
  const i = state.cards.findIndex((c) => c.id === id)
  if (i >= 0) state.cards.splice(i, 1)
  emit()
}

/** Reload the cards and the wallet from the server (after a trade that touched several cards, or a reset). */
export const reloadAll = (): Promise<void> => initData()

// ---- Mutators ----

let inFlight = false
let packMilestones: { at: number; reward: { pack: string; count: number }[] }[] = []

/** The milestones reached by packs opened since the last call (v0.9.0: they wait to be claimed,
 * nothing was paid). Each is handed out once. */
export function takePackMilestones(): { at: number; reward: { pack: string; count: number }[] }[] {
  const out = packMilestones
  packMilestones = []
  return out
}

/**
 * Buy `count` sealed packs into the inventory. Nothing is opened. Resolves null when a call is
 * already pending (double click). Throws ApiError (402 = not enough coins for all of them).
 */
export async function buyPacks(packId: string, count: number, deal = false, idemKey: string = crypto.randomUUID()): Promise<BuyResult | null> {
  if (inFlight) return null
  inFlight = true
  try {
    const r = await buyPacksApi(packId, count, idemKey, deal)
    state.wallet.coins = r.coins
    state.wallet.inventory = r.inventory
    emit()
    await refreshWallet().catch(() => {}) // the next prices
    return r
  } finally {
    inFlight = false
  }
}

/** Put today's free pack, sealed, into the inventory. Throws ApiError 409 when there is none to claim. */
export async function claimFreePack(): Promise<string> {
  const r = await claimFreePackApi()
  state.wallet.inventory = r.inventory
  emit()
  await refreshWallet().catch(() => {})
  return r.pack_id
}

/**
 * Open one sealed pack of this kind. `buy`: when none is sealed, buy one at today's price and open
 * it. Resolves null when a call is already pending. Throws ApiError (409 = none sealed, 402 = not
 * enough coins); the balance always comes from the server response.
 *
 * Milestones the pack paid are not announced here: the cards are still face down. The caller
 * takes them with `takePackMilestones()` and shows them when the last card is flipped.
 *
 * `holdVersions` (the pack overlay): the versions this pack filled stay out of the pill until the
 * overlay turns each card (`revealVersion`) or closes (`settleVersions`). Without it the pill
 * shows them at once.
 */
export async function openPack(
  packId: string, buy = false, idemKey: string = crypto.randomUUID(), holdVersions = false,
): Promise<PulledCard[] | null> {
  if (inFlight) return null
  inFlight = true
  try {
    const r = await openPackFull(packId, idemKey, buy)
    state.wallet.coins = r.coins
    state.wallet.inventory = r.inventory
    for (const p of r.pulls) put(p.collectionCard ?? p.card)
    emit()
    packMilestones.push(...r.milestones)
    const fills = r.pulls.filter((p) => p.fillsSlot).length
    if (holdVersions) heldVersions += fills
    await refreshWallet().catch(() => {}) // card counts, prices
    if (holdVersions) applySlots(r.slots ? { ...r.slots, delta: 0, filled_now: [] } : undefined) // the numbers, less what is face down
    else applySlots(r.slots) // the versions this pack filled: the pill shows them at once
    return r.pulls
  } finally {
    inFlight = false
  }
}
