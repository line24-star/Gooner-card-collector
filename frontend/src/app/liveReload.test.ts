import { describe, expect, it, vi } from 'vitest'
import { shouldReload, type ScanStatus } from '../data/api.ts'
import { getState, onLibraryChange, replaceCard, refreshWallet } from '../data/store.ts'
import { makeTestCard } from '../data/testCards.ts'
import { remountOn } from './router.ts'

const st = (state: 'idle' | 'scanning', media_version: number, last: Record<string, unknown> | null = null): ScanStatus =>
  ({ state, done: 0, total: 0, last_report: last, media_version })

describe('shouldReload with media_version', () => {
  it('reloads when the watcher changed media between polls (last_report unchanged)', () => {
    expect(shouldReload(st('idle', 3), st('idle', 4))).toBe(true)
  })
  it('does not reload when media_version and last_report are unchanged', () => {
    expect(shouldReload(st('idle', 4), st('idle', 4))).toBe(false)
  })
  it('waits while a scan is running', () => {
    expect(shouldReload(st('idle', 4), st('scanning', 9))).toBe(false)
  })
})

describe('onLibraryChange', () => {
  it('fires on a card-count change, not on a plain card or coin update', async () => {
    replaceCard(makeTestCard({ id: 1 })) // the store starts without cards
    const fn = vi.fn()
    const off = onLibraryChange(fn)
    const first = getState().cards[0]
    replaceCard({ ...first, title: 'renamed' }) // same count
    expect(fn).not.toHaveBeenCalled()
    replaceCard({ ...first, id: 987654 }) // a new card
    expect(fn).toHaveBeenCalledTimes(1)
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ coins: 5, packs_opened: 0 })))))
    await refreshWallet() // coins only
    vi.unstubAllGlobals()
    expect(fn).toHaveBeenCalledTimes(1)
    off()
    replaceCard({ ...first, id: 987655 })
    expect(fn).toHaveBeenCalledTimes(1)
  })
})

describe('remountOn', () => {
  it('remounts the page on a change and cleans everything up on unmount', () => {
    let fire = () => {}
    const off = vi.fn()
    const watch = (fn: () => void) => { fire = fn; return off }
    const cleanups: number[] = []
    let mounts = 0
    const mount = () => { const n = ++mounts; return () => { cleanups.push(n) } }
    const el = { replaceChildren: vi.fn() } as unknown as HTMLElement
    const unmount = remountOn(watch, mount)(el) as () => void
    expect(mounts).toBe(1)
    fire()
    expect(cleanups).toEqual([1])
    expect(el.replaceChildren).toHaveBeenCalledTimes(1)
    expect(mounts).toBe(2)
    unmount()
    expect(off).toHaveBeenCalled()
    expect(cleanups).toEqual([1, 2])
  })
})
