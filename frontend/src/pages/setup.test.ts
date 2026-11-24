import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPoller, shouldShowSetup, welcomeCoinsText } from './setup.ts'

describe('shouldShowSetup', () => {
  it('is forced while the collection has no cards', () => expect(shouldShowSetup('empty')).toBe(true))
  it('is not forced once there are cards', () => expect(shouldShowSetup('live')).toBe(false))
})

describe('createPoller', () => {
  afterEach(() => vi.useRealTimers())

  it('never runs two polls at once and collapses rapid triggers into one re-poll', async () => {
    vi.useFakeTimers()
    let active = 0
    let peak = 0
    let runs = 0
    const gates: (() => void)[] = []
    const task = async () => {
      runs++
      active++
      peak = Math.max(peak, active)
      await new Promise<void>((r) => gates.push(r))
      active--
    }
    const p = createPoller(task, 2000)
    void p.trigger()
    for (let i = 0; i < 5; i++) void p.trigger() // folder changed five times mid-poll
    expect(runs).toBe(1)
    gates.shift()!()
    await vi.advanceTimersByTimeAsync(0)
    expect(runs).toBe(2) // exactly one queued re-poll
    gates.shift()!()
    await vi.advanceTimersByTimeAsync(0)
    expect(gates.length).toBe(0)
    expect(runs).toBe(2)
    // one timer left: a single further run after the delay
    const next = vi.advanceTimersByTimeAsync(2000)
    await next
    expect(runs).toBe(3)
    gates.shift()!()
    await vi.advanceTimersByTimeAsync(0)
    expect(peak).toBe(1)
    p.stop()
    await vi.advanceTimersByTimeAsync(10000)
    expect(runs).toBe(3)
  })

  it('keeps polling after a task error and stops on stop()', async () => {
    vi.useFakeTimers()
    let runs = 0
    const p = createPoller(async () => { runs++; throw new Error('down') }, 1000)
    await p.trigger()
    await vi.advanceTimersByTimeAsync(1000)
    expect(runs).toBe(2)
    p.stop()
    await vi.advanceTimersByTimeAsync(5000)
    expect(runs).toBe(2)
  })
})

describe('welcomeCoinsText', () => {
  it('names the welcome coins and says the first import is unpaid', () => {
    const t = welcomeCoinsText(500)
    expect(t).toContain('500 welcome coins')
    expect(t).toMatch(/Importing your library doesn't earn coins/)
  })
  it('is empty until the amount is known', () => {
    expect(welcomeCoinsText(null)).toBe('')
    expect(welcomeCoinsText(0)).toBe('')
  })
})
