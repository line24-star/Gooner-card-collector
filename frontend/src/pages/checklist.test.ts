import { describe, expect, it } from 'vitest'
import { goLink, nextStep, type ChecklistStep } from './checklist.ts'

const step = (id: string, o: Partial<ChecklistStep> = {}): ChecklistStep => ({
  id, title: id, kind: 'x', target: 5, reward: [{ pack: 'standard', count: 1 }], progress: 0, done: false, claimed: false, ...o,
})

describe('nextStep', () => {
  it('returns the first unfinished step', () => {
    const steps = [step('a', { done: true, claimed: true }), step('b'), step('c')]
    expect(nextStep(steps)?.id).toBe('b')
  })
  it('treats a done but unclaimed step as unfinished (claim it next)', () => {
    const steps = [step('a', { done: true }), step('b')]
    expect(nextStep(steps)?.id).toBe('a')
  })
  it('returns null when everything is claimed or the list is empty', () => {
    expect(nextStep([step('a', { done: true, claimed: true })])).toBeNull()
    expect(nextStep([])).toBeNull()
  })
})

describe('goLink', () => {
  it('maps known steps and falls back to home', () => {
    expect(goLink('tag-5')).toBe('#/organize')
    expect(goLink('tag-pulled')).toBe('#/collection?filter=owned')
    expect(goLink('nope')).toBe('#/home')
  })
})
