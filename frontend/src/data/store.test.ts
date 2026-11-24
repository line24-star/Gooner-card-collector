import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ApiCard } from './api.ts'
import { getState, initData, onSlotsChange, openPack, refreshWallet, revealVersion, settleVersions, stopPriceWatch, subscribe, takePackMilestones } from './store.ts'

const apiCard = (id: number, over: Partial<ApiCard> = {}): ApiCard => ({
  id, title: `pic ${id}`, kind: 'image', thumb_url: null, file_url: `/api/media/${id}/file`, treatment: 'normal',
  owned: { normal: true }, copies: 3, tags: [], width: 1, height: 1, added_at: '2026-01-01T00:00:00',
  review_available: false, upgrade_cost: null, sell_prices: { normal: 14 }, copies_by_treatment: { normal: 3 },
  value: 17, wanted: false, fuse_from: 'normal', fuse_copies: 3,
  ...over,
})

const json = (body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } }))

/** A server that answers the wallet and the card list from what the test sets. */
function server(start: { tick: number; cards: ApiCard[]; next?: string; filled?: number }) {
  const s = { ...start, mediaCalls: 0, walletCalls: 0, pack: null as unknown }
  const fetchMock = vi.fn((url: string) => {
    if (url.startsWith('/api/economy/wallet')) {
      s.walletCalls++
      return json({ coins: 500, packs_opened: 0, slots_filled: s.filled ?? 0, slots_total: 30, market: { tick: s.tick, index: 1, mood: 'flat', change: 0, next_tick_at: s.next ?? '2099-01-01T00:00:00+00:00', tick_hours: 6 } })
    }
    if (url.startsWith('/api/media?limit=1')) return json({ total: s.cards.length, items: [] })
    if (url.startsWith('/api/media')) { s.mediaCalls++; return json({ total: s.cards.length, items: s.cards }) }
    if (url.startsWith('/api/packs/')) return json(s.pack)
    throw new Error(`unexpected request ${url}`)
  })
  vi.stubGlobal('fetch', fetchMock)
  return s
}

afterEach(() => { stopPriceWatch(); vi.unstubAllGlobals(); vi.useRealTimers() })

describe('cards follow the price update (B1)', () => {
  it('reloads every card when the wallet brings a new price update, before anyone is told', async () => {
    const s = server({ tick: 7, cards: [apiCard(4)] })
    await initData()
    expect(getState().cards[0]).toMatchObject({ value: 17, wanted: false, fuseCopies: 3, fuseFrom: 'normal' })
    const before = s.mediaCalls

    // The same price update: only the wallet is fetched.
    await refreshWallet()
    expect(s.mediaCalls).toBe(before)

    // A new one: the card is worth more, wanted, and takes one more copy to fuse.
    s.tick = 8
    s.cards = [apiCard(4, { value: 26, wanted: true, sell_prices: { normal: 28 }, fuse_copies: 4, fuse_from: null })]
    const seen: { tick: number; value: number }[] = []
    const off = subscribe((st) => seen.push({ tick: st.wallet.market.tick, value: st.cards[0].value }))
    await refreshWallet()
    off()
    expect(s.mediaCalls).toBe(before + 1)
    expect(getState().cards[0]).toMatchObject({ value: 26, wanted: true, fuseCopies: 4, fuseFrom: null, sellPrices: { normal: 28 } })
    // No listener ever saw the new wallet next to the old cards.
    expect(seen).toEqual([{ tick: 8, value: 26 }])
  })

  it('fetches the wallet by itself when the price update is due, on any page', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-11-14T11:59:00Z'))
    const s = server({ tick: 7, cards: [apiCard(4)], next: '2026-11-14T12:00:00+00:00' })
    await initData()
    const wallets = s.walletCalls
    await vi.advanceTimersByTimeAsync(30_000)
    expect(s.walletCalls).toBe(wallets) // not yet
    s.tick = 8
    s.next = '2026-11-14T18:00:00+00:00'
    s.cards = [apiCard(4, { value: 30 })]
    await vi.advanceTimersByTimeAsync(40_000)
    expect(s.walletCalls).toBe(wallets + 1)
    expect(getState().wallet.market.tick).toBe(8)
    expect(getState().cards[0].value).toBe(30)
    // The server was not there yet: it asks again a little later instead of giving up.
    s.next = '2026-11-14T18:00:00+00:00'
    vi.setSystemTime(new Date('2026-11-14T18:00:05Z'))
    await refreshWallet() // still tick 8, and the next update is already past
    const again = s.walletCalls
    s.tick = 9
    s.next = '2026-11-15T00:00:00+00:00'
    await vi.advanceTimersByTimeAsync(20_000)
    expect(s.walletCalls).toBe(again + 1)
    expect(getState().wallet.market.tick).toBe(9)
  })
})

describe('milestones a pack paid wait for the reveal (B3)', () => {
  it('hands them to the overlay once, instead of announcing them while the cards are face down', async () => {
    const s = server({ tick: 7, cards: [apiCard(4)] })
    await initData()
    takePackMilestones()
    s.pack = {
      pack_id: 'standard', cost: 100, coins: 800, replayed: false, milestones: [400, 50], inventory: {},
      pulls: [{ media_id: 4, treatment: 'foil', outcome: 'version', near_miss: false, coins: 0, upgraded_to: null, card: apiCard(4), is_new: false }],
    }
    // toast() needs a page: if the store still announced the milestone itself, this would throw.
    const pulls = await openPack('standard', false, 'key-b3')
    expect(pulls?.[0].outcome).toBe('version')
    expect(takePackMilestones()).toEqual([400, 50])
    expect(takePackMilestones()).toEqual([])
  })
})

describe('v0.8.0: the versions of a pack wait for their cards (4.4)', () => {
  const pack = (filled: number) => ({
    pack_id: 'standard', cost: 100, coins: 800, replayed: false, milestones: [], inventory: {},
    slots: { filled, total: 30, delta: 2, filled_now: [{ media_id: 4, treatment: 'foil' }, { media_id: 5, treatment: 'holo' }], emptied: [], total_delta: 0 },
    pulls: [
      { media_id: 4, treatment: 'foil', outcome: 'version', near_miss: false, coins: 0, upgraded_to: null, card: apiCard(4), is_new: false, fills_slot: true },
      { media_id: 5, treatment: 'holo', outcome: 'new', near_miss: false, coins: 0, upgraded_to: null, card: apiCard(5), is_new: true, fills_slot: true },
      { media_id: 4, treatment: 'normal', outcome: 'duplicate', near_miss: false, coins: 0, upgraded_to: null, card: apiCard(4), is_new: false, already_owned: true },
    ],
  })

  it('held back: the pill goes up one card at a time, whatever reloads the wallet meanwhile', async () => {
    const s = server({ tick: 7, cards: [apiCard(4)], filled: 10 })
    await initData()
    const deltas: number[] = []
    const off = onSlotsChange((c) => deltas.push(c.delta))
    s.pack = pack(12)
    s.filled = 12 // the server already counts both
    await openPack('standard', false, 'key-v1', true)
    expect(getState().wallet.slotsFilled).toBe(10) // both cards are face down
    expect(deltas).toEqual([])
    await refreshWallet()
    expect(getState().wallet.slotsFilled).toBe(10)

    revealVersion(4, 'foil')
    expect(getState().wallet.slotsFilled).toBe(11)
    await refreshWallet()
    expect(getState().wallet.slotsFilled).toBe(11)
    revealVersion(5, 'holo')
    expect(getState().wallet.slotsFilled).toBe(12)
    expect(getState().wallet.completeness).toBeCloseTo(12 / 30)
    expect(deltas).toEqual([1, 1])
    revealVersion(5, 'holo') // nothing is held back any more
    settleVersions()
    expect(getState().wallet.slotsFilled).toBe(12)
    expect(deltas).toEqual([1, 1])
    off()
  })

  it('closing with cards face down counts the rest at once', async () => {
    const s = server({ tick: 7, cards: [apiCard(4)], filled: 10 })
    await initData()
    const deltas: number[] = []
    const off = onSlotsChange((c) => deltas.push(c.delta))
    s.pack = pack(12)
    s.filled = 12
    await openPack('standard', false, 'key-v2', true)
    revealVersion(4, 'foil')
    settleVersions()
    expect(getState().wallet.slotsFilled).toBe(12)
    expect(deltas).toEqual([1, 1])
    off()
  })

  it('not held back: the pill shows the pack at once', async () => {
    const s = server({ tick: 7, cards: [apiCard(4)], filled: 10 })
    await initData()
    const deltas: number[] = []
    const off = onSlotsChange((c) => deltas.push(c.delta))
    s.pack = pack(12)
    s.filled = 12
    await openPack('standard', false, 'key-v3')
    expect(getState().wallet.slotsFilled).toBe(12)
    expect(deltas).toEqual([2])
    off()
  })
})
