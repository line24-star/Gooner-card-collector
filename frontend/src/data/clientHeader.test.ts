import { afterEach, describe, expect, it, vi } from 'vitest'
import { CLIENT_HEADER, removeTag, reviewCard, withClient } from './api.ts'
import { markTutorial, resetProgress } from '../help/progress.ts'
import { stopServer } from '../lifecycle.ts'

afterEach(() => vi.unstubAllGlobals())

const ok = (body: unknown = {}, status = 200) =>
  Promise.resolve(new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

const headerOf = (init: RequestInit | undefined, name: string) => new Headers(init?.headers).get(name)

describe('mutating requests carry the X-MCO-Client header (CSRF guard)', () => {
  it('withClient adds it for non-GET methods only and keeps other headers', () => {
    expect(headerOf(withClient({ method: 'POST', headers: { 'Idempotency-Key': 'k' } }), CLIENT_HEADER)).toBe('1')
    expect(headerOf(withClient({ method: 'POST', headers: { 'Idempotency-Key': 'k' } }), 'Idempotency-Key')).toBe('k')
    expect(headerOf(withClient({ method: 'DELETE' }), CLIENT_HEADER)).toBe('1')
    expect(headerOf(withClient(undefined), CLIENT_HEADER)).toBeNull()
    expect(headerOf(withClient({ method: 'GET' }), CLIENT_HEADER)).toBeNull()
  })

  it('api.ts POST and DELETE helpers send it', async () => {
    const f = vi.fn((url: string) => (url.endsWith('/review')
      ? ok({ coins_awarded: 0, coins: 1, card: { id: 1, title: '', kind: 'image', thumb_url: null, file_url: '', treatment: 'base', owned: {}, copies: 1, tags: [], width: 1, height: 1, added_at: null, review_available: false, upgrade_cost: null, sell_prices: {} } })
      : ok(null, 204)))
    vi.stubGlobal('fetch', f)
    await removeTag(1, 2)
    await reviewCard(1)
    for (const call of f.mock.calls) expect(headerOf((call as unknown as [string, RequestInit])[1], CLIENT_HEADER)).toBe('1')
  })

  it('tutorial progress PUT/DELETE and stopServer send it', async () => {
    const f = vi.fn(() => ok({}))
    vi.stubGlobal('fetch', f)
    await markTutorial('welcome', 'completed')
    await resetProgress()
    await stopServer()
    expect(f).toHaveBeenCalledTimes(3)
    for (const call of f.mock.calls) expect(headerOf((call as unknown as [string, RequestInit])[1], CLIENT_HEADER)).toBe('1')
    expect(headerOf((f.mock.calls[2] as unknown as [string, RequestInit])[1], 'X-MCO-Action')).toBe('shutdown')
  })
})
