import { describe, expect, it } from 'vitest'
import type { Card } from '../data/types.ts'
import { moreBatch, nextOrganizeQueue, paysText, QUICK_TAGS, quickTags, rushState, rushWaitText, taggableMore } from './organize.ts'

function card(id: number, title: string, o: Partial<Card> = {}): Card {
  return {
    id, title, kind: 'image', imageUrl: '', treatment: 'normal', tags: [], copies: 0, addedAt: '',
    thumbUrl: null, fileUrl: null, owned: {}, tagIds: [], reviewAvailable: false, reviewReward: null,
    upgradeCost: null, sellPrices: {}, sellExtras: {}, copiesByTreatment: {}, value: 0, printRun: 0, printTier: '', trend: 0,
    wanted: false, fuseFrom: null, fuseCopies: 3, number: 0, code: '', prices: {}, plainPrices: {}, saleQuotes: {}, fuseOptions: [], nextTagReward: 0, minPack: null, titleCustom: false, fileName: '', ...o,
  }
}

describe('nextOrganizeQueue', () => {
  it('excludes tagged cards', () => {
    const q = nextOrganizeQueue([card(1, 'a', { tags: ['x'] }), card(2, 'b')])
    expect(q.map((c) => c.id)).toEqual([2])
  })
  it('orders owned special < owned normal < un-pulled', () => {
    const q = nextOrganizeQueue([
      card(1, 'a'),
      card(2, 'b', { treatment: 'normal', owned: { normal: 1 }, copies: 1 }),
      card(3, 'c', { treatment: 'special', owned: { special: 1 }, copies: 1 }),
    ], [{ id: 'normal', tier: 0 }, { id: 'special', tier: 3 }])
    expect(q.map((c) => c.id)).toEqual([3, 2, 1])
  })
  it('sorts ties by title', () => {
    const q = nextOrganizeQueue([card(1, 'b'), card(2, 'a'), card(3, 'c')])
    expect(q.map((c) => c.title)).toEqual(['a', 'b', 'c'])
  })
})

describe('tagging more', () => {
  const cards = [
    card(1, 'untagged', { nextTagReward: 10 }),
    card(2, 'two tags', { tags: ['a', 'b'], nextTagReward: 3 }),
    card(3, 'one tag', { tags: ['a'], nextTagReward: 5 }),
    card(4, 'paid out', { tags: ['a'], nextTagReward: 0 }),
    card(5, 'one tag, foil', { tags: ['a'], nextTagReward: 8 }),
  ]
  it('takes only cards that have a tag and still pay', () => {
    expect(taggableMore(cards).map((c) => c.id)).toEqual([2, 3, 5])
  })
  it('a batch is a random pick of cards that still pay, never longer than asked (v0.9.0, P20)', () => {
    const ids = (size: number, rand?: () => number) => moreBatch(cards, size, rand).map((c) => c.id)
    expect(ids(10).sort()).toEqual([2, 3, 5]) // every card that pays, whatever the order
    expect(ids(2)).toHaveLength(2)
    expect(ids(0)).toEqual([])
    // the pick follows the random numbers: the first card is the one the first number points at
    expect(ids(1, () => 0)).toEqual([2])
    expect(ids(1, () => 0.5)).toEqual([3])
    expect(ids(1, () => 0.99)).toEqual([5])
    // and over many picks every card comes first sometimes (it was always the best-paying one)
    const firsts = new Set(Array.from({ length: 200 }, () => ids(1)[0]))
    expect([...firsts].sort()).toEqual([2, 3, 5])
    // no card twice in a batch
    for (let i = 0; i < 50; i++) expect(new Set(ids(3)).size).toBe(3)
  })
  it('offers nine quick tags, leaving out the tags the card has (P4)', () => {
    const chips = Array.from({ length: 14 }, (_, i) => `t${i}`)
    expect(QUICK_TAGS).toBe(9)
    expect(quickTags(chips, undefined)).toHaveLength(9)
    expect(quickTags(chips, card(1, 'a', { tags: ['t0', 't3'] }))).toEqual(['t1', 't2', 't4', 't5', 't6', 't7', 't8', 't9', 't10'])
  })
  it('says where the tag rush stands and when the next one is ready (P19)', () => {
    const rush = { active: false, endsAt: null, available: false, multiplier: 2, minutes: 10, availableAt: null as string | null }
    expect(rushState({ ...rush, minutes: 0 })).toBe('off')
    expect(rushState({ ...rush, active: true })).toBe('on')
    expect(rushState({ ...rush, available: true })).toBe('ready')
    expect(rushState(rush)).toBe('later')
    const now = Date.parse('2026-11-16T12:00:00Z')
    expect(rushWaitText({ availableAt: '2026-11-16T17:12:00Z' }, now)).toBe('The next tag rush is ready in 5 h 12 min.')
    expect(rushWaitText({ availableAt: null }, now)).toBe('The next tag rush comes tomorrow.')
    expect(rushWaitText({ availableAt: '2026-11-16T11:00:00Z' }, now)).toBe('The next tag rush comes tomorrow.')
  })
  it('says what the next tag pays, down to nothing', () => {
    expect(paysText(5)).toBe('The next tag pays +5 coins')
    expect(paysText(1)).toBe('The next tag pays +1 coin')
    expect(paysText(0)).toMatch(/pay nothing/)
  })
})
