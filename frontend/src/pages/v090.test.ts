/** v0.9.0: the pure rules of test round 6 that the pages are built on. */
import { describe, expect, it } from 'vitest'
import { bulkRuleText, sample } from '../data/bulk.ts'
import { makeTestCard } from '../data/testCards.ts'
import type { FuseOption } from '../data/types.ts'
import { rangeChangeText } from '../ui/priceChart.ts'
import { NO_PAUSE, pauseFrom, pauseSeconds, pauseText, quickLocked, removedTagText } from '../ui/tagPause.ts'
import { BINDER_PER_PAGE, binderCardWidth, canMerge, CARD_DEFAULT, CARD_MAX, CARD_MIN, SORTS, sortCards } from './collection.ts'
import { tagChipsHTML, trendText } from './inspect.ts'

const fuse = (can: boolean): FuseOption => ({ from: 'normal', to: 'foil', copies: 3, have: can ? 3 : 1, can, net_versions: 1, empties: false, fills: true, value: 10 })

describe('Collection: "Mergeable first" (P13)', () => {
  it('is offered in the sort list', () => {
    expect(SORTS.find(([id]) => id === 'mergeable')?.[1]).toBe('Mergeable first')
  })
  it('puts cards with a merge that can be done first, then by value', () => {
    const cards = [
      makeTestCard({ id: 1, value: 900, fuseOptions: [fuse(false)] }),
      makeTestCard({ id: 2, value: 10, fuseOptions: [fuse(false), fuse(true)] }),
      makeTestCard({ id: 3, value: 50, fuseOptions: [fuse(true)] }),
      makeTestCard({ id: 4, value: 20, fuseOptions: [] }),
    ]
    expect(canMerge(cards[1])).toBe(true)
    expect(canMerge(cards[0])).toBe(false)
    expect(sortCards(cards, 'mergeable').map((c) => c.id)).toEqual([3, 2, 1, 4])
  })
})

describe('the binder: nine pockets a page, sized by the slider (P14, D7)', () => {
  it('has 3 × 3 pockets a page', () => expect(BINDER_PER_PAGE).toBe(9))
  it('follows the slider when there is room', () => {
    expect(binderCardWidth(CARD_DEFAULT, 4000)).toBe(120)
    expect(binderCardWidth(CARD_MIN, 4000)).toBe(88)
    expect(binderCardWidth(CARD_MAX, 4000)).toBe(272)
    expect(binderCardWidth(200, 4000)).toBeGreaterThan(binderCardWidth(150, 4000))
  })
  it('never grows past what lets both pages fit', () => {
    const w = binderCardWidth(CARD_MAX, 1200)
    expect(w).toBeLessThan(272)
    // two pages of three pockets (card + 12), their gaps and paddings, the rings and the binder's own padding
    expect(2 * (3 * (w + 12) + 2 * 12 + 12) + 50 + 38).toBeLessThanOrEqual(1200)
  })
  it('fits one page when the pages stack, and keeps a floor', () => {
    const w = binderCardWidth(CARD_MAX, 360, true)
    expect(3 * (w + 12) + 2 * 12 + 12 + 38).toBeLessThanOrEqual(360)
    expect(binderCardWidth(CARD_MAX, 100, true)).toBe(56)
    expect(binderCardWidth(CARD_DEFAULT, 0)).toBe(120) // not laid out yet: the slider's size
  })
})

describe('bulk tagging pays a quarter (the ruling)', () => {
  it('names the rule from the config', () => {
    expect(bulkRuleText(0.25)).toBe('Bulk tags pay a quarter')
    expect(bulkRuleText(0.5)).toBe('Bulk tags pay half')
    expect(bulkRuleText(0.4)).toBe('Bulk tags pay 40%')
    expect(bulkRuleText(1)).toBe('')
    expect(bulkRuleText(0)).toBe('Bulk tags pay 1 coin each')
  })
})

describe('sample (P20)', () => {
  it('takes `size` different items, all of them when there are fewer', () => {
    const items = [1, 2, 3, 4, 5, 6, 7, 8]
    expect(sample(items, 3)).toHaveLength(3)
    expect(new Set(sample(items, 8)).size).toBe(8)
    expect(sample(items, 20).sort()).toEqual(items)
    expect(sample(items, -1)).toEqual([])
    expect(items).toEqual([1, 2, 3, 4, 5, 6, 7, 8]) // the list itself is left alone
  })
  it('is driven by the random numbers', () => {
    expect(sample([1, 2, 3], 3, () => 0)).toEqual([1, 2, 3])
    expect(sample([1, 2, 3], 2, () => 0.999999)).toEqual([3, 1])
  })
})

describe('the tagging pause (P21)', () => {
  const now = Date.parse('2026-11-16T12:00:00Z')
  const reply = { cooldown_until: '2026-11-16T12:00:03.200Z', must_type: true }
  it('reads the pause from a tag reply', () => {
    expect(pauseFrom(reply)).toEqual({ until: now + 3200, mustType: true })
    expect(pauseFrom({ cooldown_until: null, must_type: false })).toEqual(NO_PAUSE)
    expect(pauseFrom({})).toEqual(NO_PAUSE) // an older server
    expect(pauseFrom({ cooldown_until: 'nonsense', must_type: true })).toEqual(NO_PAUSE)
  })
  it('counts the seconds down, rounded up, to zero', () => {
    const p = pauseFrom(reply)
    expect(pauseSeconds(p, now)).toBe(4)
    expect(pauseSeconds(p, now + 1300)).toBe(2)
    expect(pauseSeconds(p, now + 3199)).toBe(1)
    expect(pauseSeconds(p, now + 3200)).toBe(0)
    expect(pauseSeconds(p, now + 9000)).toBe(0)
    expect(pauseSeconds(NO_PAUSE, now)).toBe(0)
  })
  it('locks the quick tags only while the pause runs', () => {
    const p = pauseFrom(reply)
    expect(quickLocked(p, now)).toBe(true)
    expect(quickLocked(p, now + 3200)).toBe(false)
    expect(quickLocked(NO_PAUSE, now)).toBe(false)
    expect(quickLocked({ until: now + 3000, mustType: false }, now)).toBe(false)
  })
  it('says what to do next, with the countdown, and never accuses', () => {
    const p = pauseFrom(reply)
    expect(pauseText(p, now)).toBe('Slow down: type the next tag, or wait 4 s.')
    expect(pauseText(p, now + 2500)).toBe('Slow down: type the next tag, or wait 1 s.')
    expect(pauseText(p, now + 4000)).toBe('')
    expect(pauseText(NO_PAUSE, now)).toBe('')
    expect(pauseText(p, now)).not.toMatch(/cheat|spam|abus|bot/i)
  })
  it('says that a tag sent in the pause paid nothing', () => {
    const p = pauseFrom(reply)
    expect(pauseText(p, now, true)).toBe('That tag was added, but it paid nothing: it was sent during the pause. Slow down: type the next tag, or wait 4 s.')
    expect(pauseText(p, now + 5000, true)).toBe('That tag was added, but it paid nothing: it was sent during the pause.')
  })
})

describe('removing a tag (P9)', () => {
  it('names the coins taken back, with a real minus sign', () => {
    expect(removedTagText('beach', 10)).toBe('Removed "beach" · −10 coins')
    expect(removedTagText('beach', 1)).toBe('Removed "beach" · −1 coin')
    expect(removedTagText('beach', 1234)).toBe(`Removed "beach" · −${(1234).toLocaleString()} coins`)
    expect(removedTagText('beach', 0)).toBe('Removed "beach". It had paid nothing.')
  })
  it('a tag chip is a button that removes its tag', () => {
    const html = tagChipsHTML({ tags: ['beach', 'a<b'], tagIds: [7, 9] })
    expect(html.match(/<button type="button"/g)).toHaveLength(2)
    expect(html).toContain('data-remove="7" aria-label="Remove tag beach"')
    expect(html).toContain('data-remove="9" aria-label="Remove tag a&#60;b"') // the name is escaped
    expect(html).toContain('<span class="chip-x" aria-hidden="true">×</span>')
    expect(html).not.toContain("a<b")
  })
})

describe('the price change names its range (P10)', () => {
  it('says which range the number covers', () => {
    expect(rangeChangeText(-0.03, 'day')).toBe('▼ 3% in 24 h')
    expect(rangeChangeText(0.12, 'month')).toBe('▲ 12% this month')
    expect(rangeChangeText(0.051, 'week')).toBe('▲ 5% this week')
  })
  it('says nothing for no change, or says so when asked', () => {
    expect(rangeChangeText(0.004, 'day')).toBe('')
    expect(rangeChangeText(0, 'week', true)).toBe('no change this week')
  })
  it('the card\'s trend is the 24-hour change and is labelled so, never "today"', () => {
    expect(trendText(0.04)).toBe('▲ 4% in 24 h')
    expect(trendText(-0.02)).toBe('▼ 2% in 24 h')
    expect(trendText(0)).toBe('')
  })
})
