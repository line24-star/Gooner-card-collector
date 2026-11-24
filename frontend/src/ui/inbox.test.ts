import { describe, expect, it } from 'vitest'
import { valueChangeText } from '../app/shell.ts'
import { makeTestCard } from '../data/testCards.ts'
import type { AppState } from '../data/store.ts'
import { tickClockTimes } from './countdown.ts'
import { newsCards, newsLine } from '../data/marketApi.ts'
import { newsNamesHTML } from './newsNames.ts'
import { attention, buildMessages, expired, offerText, offerWarning, rushUseful, unseenCount, type Message } from './inbox.ts'

/** A moment before the clocks used in these tests run out. */
const EARLY = Date.parse('2026-11-14T03:00:00Z')

type S = Pick<AppState, 'wallet' | 'cards' | 'packTypes'>

function state(over: Partial<S['wallet']> = {}, cards: S['cards'] = []): S {
  const wallet = {
    coins: 100, cardsOwned: cards.length, offer: null, deal: null,
    market: { tick: 7, index: 1, mood: 'flat', change: 0, nextTickAt: '2026-11-14T06:00:00+00:00', tickHours: 6 },
    rush: { active: false, endsAt: null, available: false, multiplier: 2, minutes: 10 },
    ...over,
  } as unknown as S['wallet']
  const packTypes = [{ id: 'standard', name: 'Standard Pack', blurb: '', cost: 100, size: 5, hue: 230, odds: {} }]
  return { wallet, cards, packTypes }
}

describe('messages', () => {
  it('has nothing to say when nothing is going on', () => {
    expect(buildMessages(state())).toEqual([])
  })
  it("puts a collector's offer first, with what it is worth against a plain sale", () => {
    const card = makeTestCard({ id: 4, title: 'Sunset', copies: 2 })
    const s = state({
      offer: { mediaId: 4, treatment: 'normal', price: 339, marketPrice: 345, sellPrice: 293, expiresAt: '2026-11-14T06:00:00+00:00' },
      deal: { pack: 'standard', discount: 0.3, fullPrice: 100, price: 70, left: 2, endsAt: '2026-11-14T04:00:00+00:00' },
    }, [card])
    const list = buildMessages(s, EARLY)
    expect(list.map((m) => m.kind)).toEqual(['offer', 'deal'])
    expect(list[0].title).toBe('A collector offers 🪙 339 for your Sunset')
    expect(list[0].body).toBe('Selling on the market pays 293 today, so this is 46 more. It is 2% below the market price.')
    expect(list[0].offer?.lastCopy).toBe(false)
    expect(list[1].title).toBe('Deal: Standard Pack −30%')
  })
  it('warns when the offer is for the only copy', () => {
    const s = state({ offer: { mediaId: 4, treatment: 'normal', price: 50, marketPrice: 50, sellPrice: 42, expiresAt: 'x' } }, [makeTestCard({ id: 4, copies: 1 })])
    expect(buildMessages(s)[0].offer?.lastCopy).toBe(true)
    expect(offerText(50, 42, 50)).toMatch(/right at the market price/)
  })
  it('says which slot an offer empties (7.2), and nothing when a copy stays', () => {
    const card = makeTestCard({ id: 4, copies: 3, copiesByTreatment: { normal: 2, holo: 1 } })
    const s = state({ offer: { mediaId: 4, treatment: 'holo', price: 50, marketPrice: 50, sellPrice: 42, expiresAt: 'x', copies: 1, emptiesSlot: true } }, [card])
    const o = buildMessages(s)[0].offer!
    expect(o).toMatchObject({ treatment: 'holo', emptiesSlot: true, lastCopy: false })
    expect(offerWarning(o)).toBe('Accepting empties the Holo slot (−1 version).')
    expect(offerWarning({ treatment: 'normal', emptiesSlot: false, lastCopy: false })).toBe('')
    expect(offerWarning({ treatment: 'foil', emptiesSlot: true, lastCopy: true }))
      .toBe('Accepting empties the Foil slot (−1 version). This is your only copy: the card leaves your collection.')
  })
  it('a wanted card says its bonus in coins (PT9)', () => {
    const one = [makeTestCard({ id: 1, title: 'A', copies: 1, wanted: true, treatment: 'foil', sellPrices: { foil: 31 }, plainPrices: { foil: 24 } })]
    expect(buildMessages(state({}, one), EARLY)[0].body).toBe('Your Foil copy sells for 🪙 31 instead of 24 until the next price update.')
  })
  it('an offer for a card that is gone is no message', () => {
    expect(buildMessages(state({ offer: { mediaId: 99, treatment: 'normal', price: 5, marketPrice: 5, sellPrice: 4, expiresAt: 'x' } }))).toEqual([])
  })
  it('tells about wanted cards and a ready tag rush', () => {
    const cards = [makeTestCard({ id: 1, title: 'A', copies: 1, wanted: true, nextTagReward: 5 }), makeTestCard({ id: 2, copies: 1, wanted: true }), makeTestCard({ id: 3, copies: 0, wanted: true })]
    const list = buildMessages(state({ rush: { active: false, endsAt: null, available: true, multiplier: 2, minutes: 10 } }, cards), EARLY)
    expect(list.map((m) => m.kind)).toEqual(['wanted', 'rush'])
    expect(list[0].title).toBe('Collectors want 2 of your cards')
  })
  it('counts only messages not seen before; a new offer is a new message', () => {
    const offer = (expiresAt: string) => state({ offer: { mediaId: 4, treatment: 'normal', price: 5, marketPrice: 5, sellPrice: 4, expiresAt } }, [makeTestCard({ id: 4, copies: 2 })])
    const first = buildMessages(offer('a'))
    const seen = new Set(first.map((m) => m.id))
    expect(unseenCount(first, new Set())).toBe(1)
    expect(unseenCount(first, seen)).toBe(0)
    expect(unseenCount(buildMessages(offer('b')), seen)).toBe(1)
  })
})

describe('the value pill', () => {
  it('shows which way the collection moved at the last update', () => {
    expect(valueChangeText(0.024)).toBe('▲ 2.4%')
    expect(valueChangeText(-0.003)).toBe('▼ 0.3%')
    expect(valueChangeText(0.02)).toBe('▲ 2%')
    expect(valueChangeText(0.126)).toBe('▲ 13%')
    expect(valueChangeText(0)).toBe('– 0%')
    expect(valueChangeText(0.0002)).toBe('– 0%')
  })
})

describe('when prices change', () => {
  const utc = (d: Date) => `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`
  it('lists the times of day of a 6-hour rhythm', () => {
    expect(tickClockTimes('2026-11-14T06:00:00+00:00', 6, utc)).toBe('00:00, 06:00, 12:00 and 18:00')
    expect(tickClockTimes('2026-11-14T18:00:00+00:00', 12, utc)).toBe('06:00 and 18:00')
    expect(tickClockTimes('2026-11-14T00:00:00+00:00', 24, utc)).toBe('00:00')
  })
  it('says nothing when the next update is unknown', () => {
    expect(tickClockTimes(null, 6)).toBe('')
    expect(tickClockTimes('bad', 6)).toBe('')
    expect(tickClockTimes('2026-11-14T06:00:00+00:00', 0)).toBe('')
  })
})

describe('news', () => {
  it('names the cards, yours first', () => {
    const l = newsLine({ kind: 'hype', text: 'Its price jumped.', media_ids: [1, 2], at: '', cards: [{ id: 1, title: 'Sea', owned: false }, { id: 2, title: 'Sunset', owned: true }] })
    expect(l).toEqual({ names: 'Sunset (yours), Sea', text: 'Its price jumped.', mine: true })
  })
  it('counts the rest and copes with no cards', () => {
    const cards = [1, 2, 3, 4, 5].map((id) => ({ id, title: `C${id}`, owned: false }))
    expect(newsLine({ kind: 'reprint', text: 't', media_ids: [], at: '', cards }).names).toBe('C1, C2, C3 and 2 more')
    expect(newsLine({ kind: 'x', text: 't', media_ids: [], at: '' })).toEqual({ names: '', text: 't', mine: false })
  })
  it('every card name is a link to its card panel (PT2)', () => {
    const n = { kind: 'hype', text: 't', media_ids: [], at: '', cards: [{ id: 1, title: 'Sea & <b>', owned: false }, { id: 2, title: 'Sunset', owned: true }, { id: 3, title: 'C', owned: false }, { id: 4, title: 'D', owned: false }] }
    expect(newsCards(n)).toMatchObject({ more: 1, mine: true })
    const html = newsNamesHTML(n)
    expect(html.match(/data-news-card="(\d+)"/g)).toEqual(['data-news-card="2"', 'data-news-card="1"', 'data-news-card="3"'])
    expect(html).toContain('(yours)')
    expect(html).toContain('and 1 more')
    expect(html).not.toContain('<b>')
    expect(newsNamesHTML({ kind: 'x', text: 't', media_ids: [], at: '' })).toBe('')
  })
})

describe('the Messages button asks for attention', () => {
  const now = Date.parse('2026-11-14T12:00:00Z')
  const msg = (id: string, kind: Message['kind'], until?: string): Message => ({ id, kind, icon: '', title: id, body: '', until })

  it('is quiet when everything was seen', () => {
    expect(attention([], new Set(), now)).toEqual({ count: 0, level: 'none', label: '', until: null })
    expect(attention([msg('a', 'deal', '2026-11-14T15:00:00Z')], new Set(['a']), now).level).toBe('none')
  })

  it('names what waits and shows its clock', () => {
    const a = attention([msg('w', 'wanted', '2026-11-14T18:00:00Z'), msg('d', 'deal', '2026-11-14T15:00:00Z')], new Set(), now)
    expect(a).toEqual({ count: 2, level: 'new', label: 'Wanted', until: '2026-11-14T18:00:00Z' })
    expect(attention([msg('r', 'rush')], new Set(), now)).toEqual({ count: 1, level: 'new', label: 'Tag rush', until: null })
  })

  it('is urgent for an offer and for anything that ends within the hour', () => {
    const offer = attention([msg('w', 'wanted', '2026-11-14T18:00:00Z'), msg('o', 'offer', '2026-11-14T17:00:00Z')], new Set(), now)
    expect(offer).toMatchObject({ level: 'urgent', label: 'Offer', until: '2026-11-14T17:00:00Z', count: 2 })
    const soon = attention([msg('w', 'wanted', '2026-11-14T18:00:00Z'), msg('d', 'deal', '2026-11-14T12:40:00Z')], new Set(), now)
    expect(soon).toMatchObject({ level: 'urgent', label: 'Deal', until: '2026-11-14T12:40:00Z' })
    // seen messages no longer count, however soon they end
    expect(attention([msg('d', 'deal', '2026-11-14T12:40:00Z'), msg('w', 'wanted', '2026-11-14T18:00:00Z')], new Set(['d']), now).level).toBe('new')
  })
})

describe('v0.7.1: a clock that ran out (B2)', () => {
  const END = '2026-11-14T06:00:00+00:00'
  const end = Date.parse(END)
  const offer = (expiresAt: string) => state({ offer: { mediaId: 4, treatment: 'normal', price: 339, marketPrice: 345, sellPrice: 293, expiresAt } }, [makeTestCard({ id: 4, copies: 2, wanted: true })])

  it('a message is over at the moment its countdown reads "now"', () => {
    expect(expired(END, end - 1500)).toBe(false)
    expect(expired(END, end - 900)).toBe(true) // under a second left: the clock already says "now"
    expect(expired(END, end + 60_000)).toBe(true)
    expect(expired(null, end)).toBe(false) // no clock: it never runs out
    expect(expired('not a date', end)).toBe(false)
  })

  it('an expired message is left out at once, so no clock is ever started for a time that is past', () => {
    expect(buildMessages(offer(END), end - 5000).map((m) => m.kind)).toEqual(['offer', 'wanted'])
    for (const now of [end - 900, end, end + 1, end + 3600_000]) {
      const list = buildMessages(offer(END), now)
      expect(list).toEqual([])
      expect(attention(list, new Set(), now)).toEqual({ count: 0, level: 'none', label: '', until: null })
    }
    // Whatever the moment, the clock next to the envelope is one that still runs.
    for (let now = end - 10_000; now < end + 10_000; now += 700) {
      const a = attention(buildMessages(offer(END), now), new Set(), now)
      if (a.until) expect(expired(a.until, now)).toBe(false)
    }
  })
})

describe('v0.7.1: what a message says', () => {
  it('never calls a lower offer a gain (B5)', () => {
    expect(offerText(189, 246, 193)).toBe('Selling on the market pays 246 today, so this is 57 less. It is 2% below the market price.')
    expect(offerText(339, 293, 345)).toContain('so this is 46 more')
    expect(offerText(293, 293, 345)).toContain('so this is the same')
    expect(offerText(189, 246, 193)).not.toContain('-57')
  })

  it('the tag rush is a message only while a tag still pays (D2)', () => {
    const rush = { active: false, endsAt: null, available: true, multiplier: 2, minutes: 10 }
    const paidOut = [makeTestCard({ id: 1, copies: 1, tags: ['a'], nextTagReward: 0 })]
    expect(rushUseful(paidOut)).toBe(false)
    expect(buildMessages(state({ rush }, paidOut), EARLY)).toEqual([])
    const pays = [makeTestCard({ id: 1, copies: 1, tags: ['a'], nextTagReward: 1 })]
    expect(buildMessages(state({ rush }, pays), EARLY).map((m) => m.kind)).toEqual(['rush'])
    expect(rushUseful([makeTestCard({ copies: 0, nextTagReward: 10 })])).toBe(false) // not pulled: it can't be tagged
  })
})
