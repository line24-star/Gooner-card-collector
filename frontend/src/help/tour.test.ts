import { describe, expect, it } from 'vitest'
import { tourHost } from './tour.ts'

const stubDoc = (dialogs: string[]) => ({
  body: 'BODY',
  querySelectorAll: (sel: string) => (sel === 'dialog[open]' ? dialogs : []),
}) as unknown as Document

describe('tourHost', () => {
  it('uses body when no dialog is open', () => {
    expect(tourHost(stubDoc([]))).toBe('BODY')
  })
  it('uses the topmost (last) open dialog', () => {
    expect(tourHost(stubDoc(['A', 'B']))).toBe('B')
  })
})

import { watchHostClose } from './tour.ts'

describe('watchHostClose', () => {
  it('fires on close and stops after unbinding', () => {
    const handlers = new Set<() => void>()
    const host = {
      addEventListener: (_: string, h: () => void) => handlers.add(h),
      removeEventListener: (_: string, h: () => void) => handlers.delete(h),
    }
    let n = 0
    const unbind = watchHostClose(host, () => n++)
    handlers.forEach((h) => h())
    expect(n).toBe(1)
    unbind()
    expect(handlers.size).toBe(0)
  })
})

import { nextShowable, type TourStep } from './tour.ts'

describe('v0.7.1: a step whose target is not on screen is skipped (D4)', () => {
  const steps: TourStep[] = [
    { title: 'intro', body: '' }, // no target: always shown
    { target: '#a', title: 'a', body: '' },
    { target: '#gone', title: 'gone', body: '' },
    { target: '#b', title: 'b', body: '' },
    { target: '#gone-too', title: 'gone too', body: '' },
  ]
  const visible = (s: TourStep) => s.target === '#a' || s.target === '#b'

  it('walks forward and back over the steps that can be shown', () => {
    expect(nextShowable(steps, 0, 1, visible)).toBe(0)
    expect(nextShowable(steps, 2, 1, visible)).toBe(3) // "gone" is skipped
    expect(nextShowable(steps, 4, 1, visible)).toBe(-1) // nothing after "b": the tour is finished
    expect(nextShowable(steps, 2, -1, visible)).toBe(1)
    expect(nextShowable(steps, -1, -1, visible)).toBe(-1)
  })
  it('finds nothing when no target is on screen: the tour does not run (and pays nothing)', () => {
    const organize: TourStep[] = [{ target: '#input', title: '', body: '' }, { target: '#chips', title: '', body: '' }]
    expect(nextShowable(organize, 0, 1, () => false)).toBe(-1)
  })
})
