// The vitest environment is plain node (no jsdom/happy-dom installed), so only the pure functions are tested.
import { describe, expect, it } from 'vitest'
import { highlight, suggestionRows } from './tagInput.ts'

const t = (id: number, name: string, count = 1) => ({ id, name, count })

describe('suggestionRows', () => {
  it('puts matches before related', () => {
    const rows = suggestionRows([t(1, 'cat')], [t(2, 'dog')], 'c', 8)
    expect(rows.map((r) => [r.name, r.group])).toEqual([['cat', 'match'], ['dog', 'related']])
  })
  it('carries the count', () => {
    expect(suggestionRows([t(1, 'cat', 12)], [], 'c', 8)[0].count).toBe(12)
  })
  it('drops names already shown as a match from related', () => {
    const rows = suggestionRows([t(1, 'cat')], [t(1, 'cat'), t(3, 'CAT'), t(2, 'dog')], 'c', 8)
    expect(rows.map((r) => r.name)).toEqual(['cat', 'dog'])
  })
  it('respects the limit across both groups', () => {
    const rows = suggestionRows([t(1, 'a'), t(2, 'b')], [t(3, 'c'), t(4, 'd')], '', 3)
    expect(rows.map((r) => r.name)).toEqual(['a', 'b', 'c'])
  })
  it('handles empty inputs and a zero limit', () => {
    expect(suggestionRows([], [], 'x', 8)).toEqual([])
    expect(suggestionRows([t(1, 'a')], [], '', 0)).toEqual([])
    expect(suggestionRows([], [t(1, 'a')], '', 8).map((r) => r.group)).toEqual(['related'])
  })
})

describe('highlight', () => {
  it('escapes HTML', () => {
    expect(highlight('<b>&"x"', '')).toBe('&#60;b&#62;&#38;&#34;x&#34;')
  })
  it('marks the typed part case-insensitively, keeping the original case', () => {
    expect(highlight('Sunset', 'SUN')).toBe('<mark>Sun</mark>set')
    expect(highlight('big cat', 'cat')).toBe('big <mark>cat</mark>')
  })
  it('escapes inside and around the mark', () => {
    expect(highlight('a<b>c', '<b>')).toBe('a<mark>&#60;b&#62;</mark>c')
  })
  it('returns the plain escaped name with no typed text or no match', () => {
    expect(highlight('cat', '')).toBe('cat')
    expect(highlight('cat', '   ')).toBe('cat')
    expect(highlight('cat', 'zzz')).toBe('cat')
  })
})
