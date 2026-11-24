import { describe, expect, it } from 'vitest'
import { makeTestCard } from '../data/testCards.ts'
import { bestAction, normalizeTagInput } from './inspect.ts'

describe('normalizeTagInput', () => {
  it('trims, collapses whitespace and lowercases', () => {
    expect(normalizeTagInput('  Summer  Trip ')).toBe('summer trip')
  })
  it('rejects blank input', () => {
    expect(normalizeTagInput('   ')).toBeNull()
  })
  it('rejects names over 40 characters', () => {
    expect(normalizeTagInput('x'.repeat(41))).toBeNull()
    expect(normalizeTagInput('x'.repeat(40))).toBe('x'.repeat(40))
  })
})

describe('bestAction', () => {
  it('fusing comes first, then a wanted card, then the first tag', () => {
    const fuse = makeTestCard({ copies: 4, fuseFrom: 'normal', copiesByTreatment: { normal: 4 }, wanted: true })
    expect(bestAction(fuse, 3, (id) => id.toUpperCase())).toBe('You hold 4 NORMAL copies: merge 3 into a rarer one.')
    expect(bestAction(makeTestCard({ copies: 1, wanted: true }), 3)).toMatch(/collector wants/)
    expect(bestAction(makeTestCard({ copies: 1, tags: [], nextTagReward: 10 }), 3)).toBe('No tags yet: the first one pays +10 coins.')
  })
  it('says nothing when nothing stands out, or the card is not owned', () => {
    expect(bestAction(makeTestCard({ copies: 1, tags: ['a'], nextTagReward: 5 }), 3)).toBe('')
    expect(bestAction(makeTestCard({ copies: 0, wanted: true }), 3)).toBe('')
  })
})
