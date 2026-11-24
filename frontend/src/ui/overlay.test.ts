import { describe, expect, it, vi } from 'vitest'
import { bindOverlay, overlayOpen } from './overlay.ts'

const key = (k: string) => {
  const e = new Event('keydown', { cancelable: true })
  Object.defineProperty(e, 'key', { value: k })
  return e
}

describe('bindOverlay', () => {
  it('Escape closes the overlay and is not passed on to page handlers', () => {
    const win = new EventTarget()
    const close = vi.fn()
    bindOverlay(win, close)
    const e = key('Escape')
    const stop = vi.spyOn(e, 'stopPropagation')
    win.dispatchEvent(e)
    expect(close).toHaveBeenCalledTimes(1)
    expect(stop).toHaveBeenCalled()
    expect(e.defaultPrevented).toBe(true)
  })

  it('a route change (browser Back, hash link) closes the overlay', () => {
    const win = new EventTarget()
    const close = vi.fn()
    bindOverlay(win, close)
    win.dispatchEvent(new Event('hashchange'))
    expect(close).toHaveBeenCalledTimes(1)
  })

  it('other keys are left alone, and unbinding removes both listeners', () => {
    const win = new EventTarget()
    const close = vi.fn()
    const unbind = bindOverlay(win, close)
    win.dispatchEvent(key('a'))
    expect(close).not.toHaveBeenCalled()
    unbind()
    win.dispatchEvent(key('Escape'))
    win.dispatchEvent(new Event('hashchange'))
    expect(close).not.toHaveBeenCalled()
  })
})

describe('overlayOpen', () => {
  it('is true while a pack-opening overlay is in the document', () => {
    expect(overlayOpen({ querySelector: (s: string) => (s === '.opening' ? ({} as Element) : null) })).toBe(true)
    expect(overlayOpen({ querySelector: () => null })).toBe(false)
  })
})
