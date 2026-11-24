import { describe, expect, it } from 'vitest'
import { shouldRunFirstVisit } from './firstVisit.ts'

const done = new Map<string, 'completed' | 'skipped'>([['setup', 'completed'], ['welcome', 'skipped']])
const base = { mode: 'live' as const, progress: done, tourActive: false, id: 'organize', hash: '#/organize' }

describe('shouldRunFirstVisit', () => {
  it('runs when everything is in order', () => expect(shouldRunFirstVisit(base)).toBe(true))
  it('not while the collection is empty', () => expect(shouldRunFirstVisit({ ...base, mode: 'empty' })).toBe(false))
  it('not while a tour is active', () => expect(shouldRunFirstVisit({ ...base, tourActive: true })).toBe(false))
  it('not when the tour already has an entry', () => {
    const p = new Map(done).set('organize', 'skipped')
    expect(shouldRunFirstVisit({ ...base, progress: p })).toBe(false)
  })
  it('runs for a returning live-mode player with no setup entry (welcome done)', () => {
    expect(shouldRunFirstVisit({ ...base, progress: new Map([['welcome', 'completed']]) })).toBe(true)
  })
  it('runs for a returning player who skipped the welcome tour', () => {
    expect(shouldRunFirstVisit({ ...base, progress: new Map([['welcome', 'skipped']]) })).toBe(true)
  })
  it('not before the welcome tour is done', () => {
    expect(shouldRunFirstVisit({ ...base, progress: new Map([['setup', 'completed']]) })).toBe(false)
  })
  it('not on the setup screen', () => expect(shouldRunFirstVisit({ ...base, hash: '#/setup' })).toBe(false))
})
