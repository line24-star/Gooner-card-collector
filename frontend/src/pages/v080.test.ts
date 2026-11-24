import { afterEach, describe, expect, it, vi } from 'vitest'
import { pillDeltaText, pillTitle } from '../app/shell.ts'
import { cardHTML, copiesLabel, ringTitle, versionRingHTML } from '../cards/card.ts'
import { packsHolding } from '../data/packOdds.ts'
import { applySlots, getState, missingCount, missingVersions, onSlotsChange, versionDeltaText } from '../data/store.ts'
import { makeTestCard } from '../data/testCards.ts'
import { cellSize, dismissedMoves, emptyHTML, filterCards, priceMove, sortCards } from './collection.ts'
import { dayRows, findInPacksText, mergeProgressPct, mergeProgressText, wantedText } from './inspect.ts'
import { listNote } from './market.ts'

afterEach(() => vi.unstubAllGlobals())

describe('version helpers (0.2)', () => {
  it('missingVersions names the versions without a copy, lowest first', () => {
    expect(missingVersions(makeTestCard({ copiesByTreatment: { normal: 3, holo: 1 } }))).toEqual(['foil', 'special'])
    expect(missingVersions(makeTestCard({ copiesByTreatment: { foil: 1, holo: 2, special: 1 } }))).toEqual([])
    expect(missingVersions(makeTestCard({ copiesByTreatment: { foil: 0 } }))).toEqual(['foil', 'holo', 'special'])
  })
  it('missingCount counts pulled cards with a missing version', () => {
    const cards = [
      makeTestCard({ id: 1, copies: 1, copiesByTreatment: { normal: 1 } }),
      makeTestCard({ id: 2, copies: 3, copiesByTreatment: { foil: 1, holo: 1, special: 1 } }),
      makeTestCard({ id: 3, copies: 0 }),
    ]
    expect(missingCount(cards)).toBe(1)
  })
  it('versionDeltaText uses a real minus sign and is empty for no change', () => {
    expect(versionDeltaText(2)).toBe('+2')
    expect(versionDeltaText(-1)).toBe('−1')
    expect(versionDeltaText(0)).toBe('')
  })
})

describe('the store applies the slots of an action at once (0.1)', () => {
  it('sets the wallet numbers, tells subscribers, and hands the change to the slot listeners', () => {
    const seen: number[] = []
    const off = onSlotsChange((c) => seen.push(c.delta))
    applySlots({ filled: 42, total: 180, delta: 1, filled_now: [{ media_id: 4, treatment: 'foil' }], emptied: [], total_delta: 0 })
    expect(getState().wallet).toMatchObject({ slotsFilled: 42, slotsTotal: 180 })
    expect(getState().wallet.completeness).toBeCloseTo(42 / 180)
    // Nothing moved: the numbers are set, nobody is told about a change.
    applySlots({ filled: 42, total: 180, delta: 0, filled_now: [], emptied: [], total_delta: 0 })
    applySlots(undefined)
    // A fill and an empty in one action (net 0) is still a change for the rings.
    applySlots({ filled: 42, total: 180, delta: 0, filled_now: [{ media_id: 4, treatment: 'holo' }], emptied: [{ media_id: 4, treatment: 'foil' }], total_delta: 0 })
    off()
    applySlots({ filled: 41, total: 180, delta: -1, filled_now: [], emptied: [{ media_id: 4, treatment: 'holo' }], total_delta: 0 })
    expect(seen).toEqual([1, 0])
    expect(getState().wallet.slotsFilled).toBe(41)
  })
})

describe('the version pill (1.1)', () => {
  it('appends the delta: versions, and slots after a scan', () => {
    expect(pillDeltaText({ delta: 2, total_delta: 0 })).toBe('+2')
    expect(pillDeltaText({ delta: -1, total_delta: 0 })).toBe('−1')
    expect(pillDeltaText({ delta: 0, total_delta: 36 })).toBe('+36 slots')
    expect(pillDeltaText({ delta: 0, total_delta: 0 })).toBe('')
  })
  it('keeps the percentage and the pulled count in the tooltip', () => {
    const t = pillTitle({ owned: 12, total: 60, complete: 0.25 })
    expect(t).toContain('25%')
    expect(t).toContain('12 of the 60 cards')
  })
})

describe('the version ring and the copies badge (2.1, PT12)', () => {
  it('draws three segments, Foil, Holo, Special, filled in the treatment colour', () => {
    const html = versionRingHTML({ normal: 2, holo: 1 })
    expect(html.match(/<path /g)).toHaveLength(3)
    expect(html.match(/class="is-on"/g)).toHaveLength(1)
    expect(html).toContain('--seg:var(--r-holo)')
    expect(ringTitle({ normal: 2, holo: 1 })).toBe('Foil missing · Holo held · Special missing')
  })
  it('is on every card face unless switched off', () => {
    const card = makeTestCard({ copies: 1, copiesByTreatment: { foil: 1 }, treatment: 'foil' })
    expect(cardHTML(card)).toContain('class="vring"')
    expect(cardHTML(card, { ring: false })).not.toContain('class="vring"')
  })
  it('names the treatment of the copies', () => {
    expect(copiesLabel({ normal: 2 })).toBe('2 Normal')
    expect(copiesLabel({ foil: 1, normal: 2 })).toBe('2 Normal · 1 Foil')
    expect(copiesLabel({ normal: 2, foil: 1, holo: 1 })).toBe('2 Normal · 1 Foil · 1 Holo')
    // Under the smallest card three treatments shrink to letters; one or two stay spelled out.
    expect(copiesLabel({ normal: 2, foil: 1, holo: 1 }, true)).toBe('2 N · 1 F · 1 H')
    expect(copiesLabel({ normal: 2, foil: 1 }, true)).toBe('2 Normal · 1 Foil')
    expect(copiesLabel({ normal: 14, holo: 1 }, true)).toBe('14 Normal · 1 Holo')
    expect(copiesLabel({ normal: 7, special: 1 }, true)).toBe('7 N · 1 S') // the long form would be cut off
    expect(copiesLabel({})).toBe('')
    expect(cardHTML(makeTestCard({ copies: 3, copiesByTreatment: { normal: 2, foil: 1 } }), { showCount: true })).toContain('2 Normal · 1 Foil')
  })
})

describe('Collection: Missing versions (1.3) and the price-move badge (7.1)', () => {
  const cards = [
    makeTestCard({ id: 1, copies: 1, value: 10, copiesByTreatment: { normal: 1 } }),
    makeTestCard({ id: 2, copies: 2, value: 50, copiesByTreatment: { foil: 1, holo: 1 } }),
    makeTestCard({ id: 3, copies: 2, value: 90, copiesByTreatment: { normal: 1, foil: 1 } }),
    makeTestCard({ id: 4, copies: 3, value: 99, copiesByTreatment: { foil: 1, holo: 1, special: 1 } }),
    makeTestCard({ id: 5, copies: 0, value: 500 }),
    makeTestCard({ id: 6, copies: 1, value: 70, copiesByTreatment: { special: 1 } }),
  ]
  it('lists pulled cards that lack a version, fewest missing first, then the most valuable', () => {
    const list = sortCards(filterCards(cards, 'missing'), 'missing')
    expect(list.map((c) => c.id)).toEqual([2, 3, 6, 1])
  })
  it('an empty list still points to the packs', () => {
    expect(emptyHTML('missing', false)).toContain('#/packs')
    expect(emptyHTML('missing', false)).not.toMatch(/complete|finished|done/)
  })
  it('the cell leaves room for the value and the copies line', () => {
    expect(cellSize(110).height).toBe(Math.round(110 * 88 / 63) + 56)
  })
  it('shows a badge only with exactly one version held and a move over 3%', () => {
    const one = { copies: 2, copiesByTreatment: { normal: 1, foil: 1 } }
    expect(priceMove({ ...one, trend: 0.11 })).toEqual({ dir: 'up', text: '▲ 11%' })
    expect(priceMove({ ...one, trend: -0.04 })).toEqual({ dir: 'down', text: '▼ 4%' })
    expect(priceMove({ ...one, trend: 0.03 })).toBeNull()
    expect(priceMove({ copies: 3, copiesByTreatment: { foil: 1, holo: 1 }, trend: 0.2 })).toBeNull()
    expect(priceMove({ copies: 1, copiesByTreatment: { normal: 1 }, trend: 0.2 })).toBeNull()
    expect(priceMove({ copies: 0, copiesByTreatment: { foil: 1 }, trend: 0.2 })).toBeNull()
  })
  it('a dismissal lasts for the price update it was made in', () => {
    const raw = JSON.stringify({ tick: 7, ids: [4, 9] })
    expect([...dismissedMoves(7, raw)]).toEqual([4, 9])
    expect([...dismissedMoves(8, raw)]).toEqual([])
    expect([...dismissedMoves(7, null)]).toEqual([])
    expect([...dismissedMoves(7, 'not json')]).toEqual([])
  })
})

describe('the card panel (PT6, PT13, PT15)', () => {
  it('says the merge progress with the card\'s own count', () => {
    expect(mergeProgressText({ have: 2, copies: 3, to: 'foil' })).toBe('2 of 3 for a Foil')
    expect(mergeProgressText({ have: 9, copies: 5, to: 'holo' })).toBe('5 of 5 for a Holo')
    expect(mergeProgressPct({ have: 2, copies: 4 })).toBe(50)
    expect(mergeProgressPct({ have: 9, copies: 4 })).toBe(100)
  })
  it('says which packs hold the card and how often they give the treatment', () => {
    const packs = [
      { id: 'p', name: 'Premium Pack', cost: 480, size: 10, odds: { holo: 0.04 } },
      { id: 's', name: 'Standard Pack', cost: 100, size: 5, odds: { holo: 0.0159 } },
      { id: 'd', name: 'Deluxe Pack', cost: 180, size: 5, odds: { holo: 0.029 } },
    ]
    const holo = { id: 'holo', pack_odds: 0.0159 }
    expect(findInPacksText(holo, null, packs)).toBe('Standard Pack: 1 in 13 packs · Deluxe Pack: 1 in 7 packs · Premium Pack: 1 in 3 packs')
    expect(findInPacksText(holo, 'd', packs)).toBe('Deluxe Pack: 1 in 7 packs · Premium Pack: 1 in 3 packs')
    expect(packsHolding('p', packs).map((p) => p.id)).toEqual(['p'])
    expect(packsHolding('gone', packs).map((p) => p.id)).toEqual(['s', 'd', 'p'])
  })
  it('says the wanted bonus in coins (PT9)', () => {
    expect(wantedText({ sellPrices: { foil: 31 }, plainPrices: { foil: 24 } }, 'foil')).toBe('sells for 🪙 31 instead of 24')
    expect(wantedText({ sellPrices: { foil: 24 }, plainPrices: { foil: 24 } }, 'foil')).toBe('')
    expect(wantedText({ sellPrices: {}, plainPrices: {} }, 'foil')).toBe('')
  })
  it('turns the days into table rows, newest first, at the treatment factor', () => {
    const rows = dayRows([
      { date: '2026-11-12', price: 10, change: null, change_coins: null },
      { date: '2026-11-13', price: 11, change: 0.1, change_coins: 1 },
      { date: '2026-11-14', price: 10, change: -0.0909, change_coins: -1 },
      { date: '2026-11-15', price: 10, change: 0, change_coins: 0 },
    ], 4)
    expect(rows.map((r) => [r.price, r.change, r.dir])).toEqual([[40, '–', 'flat'], [40, '▼ 9.1%', 'down'], [44, '▲ 10%', 'up'], [40, '–', 'flat']])
    expect(rows[0].date).toBe(new Date(2026, 10, 15).toLocaleDateString([], { month: 'short', day: 'numeric' }))
  })
})

describe('the market lists (PT16)', () => {
  it('a short or empty list says why', () => {
    expect(listNote(10, 10, 'Foil', 20)).toBe('')
    expect(listNote(4, 10, 'Holo', 20)).toBe('4 of 10: a short list. Only cards whose Normal copy is worth 20 coins or more are traded.')
    expect(listNote(0, 10, 'Special', 20)).toBe('No Special card is for sale right now. Only cards whose Normal copy is worth 20 coins or more are traded.')
  })
})
