import { describe, expect, it } from 'vitest'
import { chunk, gainedCoins } from './bulk.ts'

describe('gainedCoins', () => {
  it('is the tag reward: a milestone pays packs and is claimed, nothing comes on top (v0.9.0)', () => {
    expect(gainedCoins({ coins_awarded: 12, milestones: [{ at: 0.05, reward: [{ pack: 'standard', count: 1 }] }] })).toBe(12)
  })
  it('handles no milestones and missing fields', () => {
    expect(gainedCoins({ coins_awarded: 7, milestones: [] })).toBe(7)
    expect(gainedCoins({ coins_awarded: 0 })).toBe(0)
  })
  it('ignores whatever the milestone list holds', () => {
    expect(gainedCoins({ coins_awarded: 1, milestones: [5, 'x'] })).toBe(1)
  })
})

describe('chunk', () => {
  it('splits into slices of the given size', () => {
    expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]])
  })
  it('returns no chunks for an empty list', () => {
    expect(chunk([], 3)).toEqual([])
  })
  it('treats a size below 1 as 1', () => {
    expect(chunk([1, 2], 0)).toEqual([[1], [2]])
  })
})
