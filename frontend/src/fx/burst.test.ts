// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { burstPlan, celebrate, stopCelebrations, type RarityLevel } from './burst'

// The configured vitest environment is node (no DOM), so DOM-touching tests use a tiny fake.
class FakeEl {
  tag: string
  className = ''
  textContent = ''
  parent: FakeEl | null = null
  children: FakeEl[] = []
  vars: Record<string, string> = {}
  style = { setProperty: (k: string, v: string) => { this.vars[k] = v } }
  width = 0
  height = 0
  ctxFactory: (() => unknown) | null = null
  constructor(tag: string) { this.tag = tag }
  append(c: FakeEl) { c.parent = this; this.children.push(c) }
  remove() {
    if (!this.parent) return
    this.parent.children = this.parent.children.filter((x) => x !== this)
    this.parent = null
  }
  get isConnected(): boolean {
    let n: FakeEl = this
    while (n.parent) n = n.parent
    return n === body
  }
  querySelector() { return null }
  getContext() { return this.ctxFactory ? this.ctxFactory() : null }
  setTransform() {}
}

let body: FakeEl
let canvasesCreated = 0
let withContext = false
let rafQueue: Array<(t: number) => void>
let nowMs: number

function count(el: FakeEl, cls: string): number {
  return (el.className.includes(cls) ? 1 : 0) + el.children.reduce((n, c) => n + count(c, cls), 0)
}
const total = (el: FakeEl): number => el.children.reduce((n, c) => n + 1 + total(c), 0)

function mockCtx() {
  return { clearRect() {}, fillRect() {}, setTransform() {}, globalAlpha: 1, fillStyle: '', globalCompositeOperation: '' }
}

function stubMatchMedia(reduce: boolean) {
  vi.stubGlobal('matchMedia', () => ({ matches: reduce }))
}

beforeEach(() => {
  body = new FakeEl('body')
  canvasesCreated = 0
  rafQueue = []
  nowMs = 1000
  vi.stubGlobal('document', {
    body,
    querySelector: () => null,
    createElement: (tag: string) => {
      const e = new FakeEl(tag)
      if (tag === 'canvas') {
        canvasesCreated++
        if (withContext) e.ctxFactory = mockCtx
      }
      return e
    },
  })
  vi.stubGlobal('window', { innerWidth: 800, innerHeight: 600, devicePixelRatio: 3 })
  vi.stubGlobal('requestAnimationFrame', (cb: (t: number) => void) => { rafQueue.push(cb); return rafQueue.length })
  vi.stubGlobal('cancelAnimationFrame', () => { rafQueue = [] })
  vi.spyOn(performance, 'now').mockImplementation(() => nowMs)
  stubMatchMedia(false)
  withContext = false
})

afterEach(() => {
  stopCelebrations()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('burstPlan', () => {
  it('level 0 is empty', () => {
    const p = burstPlan(0)
    expect(p.particles).toBe(0)
    expect(p.durationMs).toBe(0)
    expect(p.tint).toBe(0)
    expect(p.banner).toBe(false)
    expect(p.colors).toEqual([])
  })

  it('escalates strictly from level 1 to 3', () => {
    const [a, b, c] = ([1, 2, 3] as RarityLevel[]).map((l) => burstPlan(l))
    expect(a.particles).toBeLessThan(b.particles)
    expect(b.particles).toBeLessThan(c.particles)
    expect(a.durationMs).toBeLessThan(b.durationMs)
    expect(b.durationMs).toBeLessThan(c.durationMs)
    expect(a.tint).toBeLessThan(b.tint)
    expect(b.tint).toBeLessThan(c.tint)
    expect(a.banner).toBe(false)
    expect(b.banner && c.banner).toBe(true)
    expect(c.rings).toBeGreaterThan(b.rings)
    expect(c.rays).toBeGreaterThan(0)
    expect(c.rain).toBeGreaterThan(0)
    expect(a.shake).toBe(0)
    expect(c.shake).toBeGreaterThan(b.shake)
  })

  it('adds the pack hue to the colours of every level', () => {
    for (const l of [1, 2, 3] as RarityLevel[]) {
      expect(burstPlan(l, 120).colors).toContain('hsl(120 90% 70%)')
    }
  })

  it('scales with intensity inside the cap', () => {
    for (const l of [1, 2, 3] as RarityLevel[]) {
      const lo = burstPlan(l, 230, 0.5)
      const mid = burstPlan(l, 230, 1)
      const hi = burstPlan(l, 230, 2)
      expect(lo.particles).toBeLessThan(mid.particles)
      expect(mid.particles).toBeLessThan(hi.particles)
      expect(burstPlan(l, 230, 99).particles).toBe(hi.particles) // clamped
      expect(burstPlan(l, 230, 99).particles).toBeLessThanOrEqual(400)
      expect(hi.durationMs).toBeGreaterThan(lo.durationMs)
    }
  })
})

describe('celebrate', () => {
  it('level 0 adds nothing', () => {
    celebrate({ level: 0, host: body as unknown as HTMLElement })
    expect(total(body)).toBe(0)
    expect(canvasesCreated).toBe(0)
  })

  it('reduced motion: tint and banner only, no canvas', () => {
    stubMatchMedia(true)
    withContext = true
    celebrate({ level: 3, label: 'SPECIAL!', host: body as unknown as HTMLElement })
    expect(canvasesCreated).toBe(0)
    expect(count(body, 'burst-rays')).toBe(0)
    expect(count(body, 'burst-ring')).toBe(0)
    expect(count(body, 'burst-tint')).toBe(1)
    expect(count(body, 'burst-banner')).toBe(1)
  })

  it('without a 2d context the DOM layers still appear, no canvas stays', () => {
    celebrate({ level: 3, label: 'SPECIAL!', host: body as unknown as HTMLElement })
    expect(count(body, 'burst-canvas')).toBe(0)
    expect(count(body, 'burst-ring')).toBe(2)
    expect(count(body, 'burst-rays')).toBe(1)
    expect(count(body, 'burst-banner')).toBe(1)
  })

  it('stopCelebrations removes every layer', () => {
    withContext = true
    celebrate({ level: 3, label: 'SPECIAL!', host: body as unknown as HTMLElement })
    celebrate({ level: 2, label: 'HOLO!', host: body as unknown as HTMLElement })
    expect(total(body)).toBeGreaterThan(3)
    stopCelebrations()
    expect(total(body)).toBe(0)
    expect(rafQueue.length).toBe(0)
  })

  it('merges overlapping celebrations into one canvas and one loop, then cleans up', () => {
    withContext = true
    const host = body as unknown as HTMLElement
    celebrate({ level: 3, label: 'SPECIAL!', host })
    celebrate({ level: 2, label: 'HOLO!', host })
    celebrate({ level: 1, host })
    expect(canvasesCreated).toBe(1)
    expect(count(body, 'burst-canvas')).toBe(1)
    expect(rafQueue.length).toBe(1)

    // run frames until the loop ends itself
    let frames = 0
    while (rafQueue.length && frames < 1000) {
      expect(rafQueue.length).toBe(1)
      const cb = rafQueue.shift()!
      nowMs += 16
      cb(nowMs)
      frames++
    }
    expect(frames).toBeLessThan(1000)
    expect(rafQueue.length).toBe(0)
    expect(count(body, 'burst-canvas')).toBe(0) // canvas removed once nothing is alive
  })
})
