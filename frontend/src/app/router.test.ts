import { describe, expect, it } from 'vitest'
import { parseRoute } from './router.ts'
import { visibleRange } from '../ui/virtualGrid.ts'
import { filterCards } from '../pages/collection.ts'
import { TREATMENT_VISUALS, visualFor } from '../cards/treatments.ts'
import { FALLBACK_TREATMENTS } from '../data/mock.ts'
import { makeTestCards } from '../data/testCards.ts'

describe('parseRoute', () => {
  const ids = ['home', 'collection', 'packs']
  it('reads the page id and ignores query', () => {
    expect(parseRoute('#/collection?filter=untagged', ids, 'home')).toBe('collection')
    expect(parseRoute('#/packs', ids, 'home')).toBe('packs')
  })
  it('falls back for empty or unknown routes', () => {
    expect(parseRoute('', ids, 'home')).toBe('home')
    expect(parseRoute('#/nope', ids, 'home')).toBe('home')
  })
})

describe('visibleRange', () => {
  it('windows rows with overscan and clamps to bounds', () => {
    expect(visibleRange(0, 500, 100, 50, 2)).toEqual({ first: 0, last: 7 })
    expect(visibleRange(1000, 500, 100, 50, 2)).toEqual({ first: 8, last: 17 })
    expect(visibleRange(4900, 500, 100, 50, 2)).toEqual({ first: 47, last: 50 })
  })
})

describe('filterCards', () => {
  const cards = makeTestCards(100)
  it('filters by untagged / treatment', () => {
    expect(filterCards(cards, 'untagged').every((c) => c.tags.length === 0)).toBe(true)
    expect(filterCards(cards, 'holo').every((c) => c.treatment === 'holo')).toBe(true)
    expect(filterCards(cards, 'all')).toHaveLength(100)
  })
})

describe('treatment visuals', () => {
  it('every server treatment has a visual, escalating with tier', () => {
    for (const t of FALLBACK_TREATMENTS) expect(TREATMENT_VISUALS[t.id], t.id).toBeDefined()
    const levels = [...FALLBACK_TREATMENTS].sort((a, b) => a.tier - b.tier).map((t) => visualFor(t.id).revealLevel)
    expect(levels).toEqual([...levels].sort((a, b) => a - b))
  })
  it('unknown treatments fall back to normal', () => {
    expect(visualFor('mystery')).toBe(TREATMENT_VISUALS.normal)
  })
})
