/** Client for the card market: the market overview, buying, selling duplicates, the collector's offer and price history. */

import { ApiError, cardFromApi, withClient, type ApiCard } from './api.ts'
import type { Card, SlotsChange } from './types.ts'

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

/** One copy for sale: a card at the treatment it is listed in (v0.8.0: one list per treatment). */
export interface Listing {
  media_id: number
  /** The treatment of the copy for sale. */
  treatment: string
  /** What buying costs (the market's cut is in it). */
  price: number
  /** The market price of that copy. */
  value: number
  number: number
  code: string
  title: string
  thumb_url: string | null
  /** Copies of that treatment the player holds. */
  copies: number
  /** Buying it fills an empty version slot (a Foil, Holo or Special not held). */
  fills_slot: boolean
  print_run: number
  print_tier: string
  trend: number
  wanted: boolean
  card: Card
}

export interface WantedEntry { media_id: number; bonus: number; card: Card }
export interface Mover { media_id: number; trend: number; card: Card }
export interface NewsItem {
  kind: string; text: string; media_ids: number[]; at: string
  /** The cards this item is about, and whether you own them. */
  cards?: { id: number; title: string; owned: boolean; number?: number; code?: string }[]
}

/**
 * A news line that says which cards it is about: "Sunset, Card #000412: A card is in demand…".
 * Your own cards come first and are marked; more than three are counted.
 */
export function newsLine(n: NewsItem): { names: string; text: string; mine: boolean } {
  const cards = (n.cards ?? []).slice().sort((a, b) => Number(b.owned) - Number(a.owned))
  const shown = cards.slice(0, 3).map((c) => (c.owned ? `${c.title} (yours)` : c.title))
  const names = shown.join(', ') + (cards.length > 3 ? ` and ${cards.length - 3} more` : '')
  return { names, text: n.text, mine: cards.some((c) => c.owned) }
}

/**
 * The cards a news item names, your own first: up to three are shown, the rest are counted.
 * Each one is a link to its card panel in the Market news and in Messages.
 */
export function newsCards(n: NewsItem): { shown: { id: number; title: string; owned: boolean }[]; more: number; mine: boolean } {
  const cards = (n.cards ?? []).slice().sort((a, b) => Number(b.owned) - Number(a.owned))
  return { shown: cards.slice(0, 3), more: Math.max(0, cards.length - 3), mine: cards.some((c) => c.owned) }
}

export interface Offer {
  media_id: number
  treatment: string
  price: number
  market_price: number
  sell_price: number
  /** Copies of that treatment the player holds, and whether taking the offer empties its version slot. */
  copies?: number
  empties_slot?: boolean
  expires_at: string
  card: Card
}

export interface Market {
  tick: number
  index: number
  mood: string
  /** Change of the index over the last day, as a fraction. */
  change: number
  next_tick_at: string
  tick_hours: number
  index_history: number[]
  news: NewsItem[]
  /** Per treatment id (normal, foil, holo, special): the most valuable cards at that treatment. */
  listings: Record<string, Listing[]>
  wanted: WantedEntry[]
  movers: Mover[]
  offer: Offer | null
  collection_value: number
  /** The cards held now at the last update against the one before. */
  collection_change: { previous: number; change: number; coins: number }
  /** What the cards held now were worth at each kept update, oldest first. */
  collection_history: PricePoint[]
  duplicates: { copies: number; value: number }
  sell_rate: number
  buy_markup: number
}

type RawMarket = Omit<Market, 'listings' | 'wanted' | 'movers' | 'offer'> & {
  listings: Record<string, (Omit<Listing, 'card'> & { card: ApiCard })[]>
  wanted: (Omit<WantedEntry, 'card'> & { card: ApiCard })[]
  movers: (Omit<Mover, 'card'> & { card: ApiCard })[]
  offer: (Omit<Offer, 'card'> & { card: ApiCard }) | null
}

export async function fetchMarket(): Promise<Market> {
  const m = await call<RawMarket>('/api/market')
  return {
    ...m,
    index_history: m.index_history ?? [],
    news: m.news ?? [],
    listings: Object.fromEntries(Object.entries(m.listings ?? {}).map(
      ([t, rows]) => [t, rows.map((l) => ({ ...l, card: cardFromApi(l.card) }))])),
    wanted: (m.wanted ?? []).map((w) => ({ ...w, card: cardFromApi(w.card) })),
    movers: (m.movers ?? []).map((v) => ({ ...v, card: cardFromApi(v.card) })),
    offer: m.offer ? { ...m.offer, card: cardFromApi(m.offer.card) } : null,
  }
}

/** Buy one copy of a listed card at the treatment it is listed in. */
export const buyListed = (mediaId: number, treatment = 'normal') =>
  post<{ price: number; coins: number; media_id?: number; treatment?: string; copies?: number; milestones?: unknown[]; slots?: SlotsChange }>(
    '/api/market/buy', { media_id: mediaId, treatment })

export const sellAllDuplicates = () =>
  post<{ price: number; sold: number; coins: number; slots?: SlotsChange }>('/api/market/sell-duplicates')

export async function fetchOffer(): Promise<Offer | null> {
  const r = await call<{ offer: (Omit<Offer, 'card'> & { card: ApiCard }) | null }>('/api/market/offer')
  return r.offer ? { ...r.offer, card: cardFromApi(r.offer.card) } : null
}

export const answerOffer = (accept: boolean) =>
  post<{ accepted: boolean; price: number; coins: number; media_id?: number; treatment?: string; slots?: SlotsChange }>('/api/market/offer', { accept })

export interface PricePoint { at: string; price: number }

export interface CardHistory {
  media_id: number
  tick_hours: number
  points: PricePoint[]
  /** One row per day, the last 30, oldest first: the Normal price at the end of the day and the
   * change against the day before (null for a day without one before it). */
  days?: { date: string; price: number; change: number | null; change_coins: number | null }[]
  /** This card's factor per treatment id: a copy's price is the Normal price times this. */
  treatment_value: Record<string, number>
  next_tick_at: string
  value: number
  print_run: number
  print_tier: string
  trend: number
  /** v0.9.0: the change over the last update, 24 hours, 7 days and 30 days (fractions). `trend` equals `day`. */
  changes?: { update: number; day: number; week: number; month: number }
  wanted: boolean
}

export const fetchCardHistory = (id: number) => call<CardHistory>(`/api/market/cards/${id}/history`)
