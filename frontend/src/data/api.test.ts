import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, cardFromApi, fetchAllCards, openPackApi, shouldReload, type ApiCard, type ScanStatus } from './api.ts'
import { affordable, buyPacks, completeness, getState, initData, openPack, packCost, pctText, sealedTotal, versionsText } from './store.ts'

const apiCard = (id: number, over: Partial<ApiCard> = {}): ApiCard => ({
  id,
  title: `pic ${id}`,
  kind: 'image',
  thumb_url: `/api/media/${id}/thumb`,
  file_url: `/api/media/${id}/file`,
  treatment: 'foil',
  owned: { foil: true },
  copies: 2,
  tags: [{ id: 7, name: 'pets', parent_id: null }],
  width: 10,
  height: 10,
  added_at: '2026-01-01T00:00:00',
  review_available: true,
  upgrade_cost: 50,
  sell_prices: { foil: 30 },
  ...over,
})

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }))

afterEach(() => vi.unstubAllGlobals())

describe('api client', () => {
  it('cardFromApi maps fields', () => {
    const c = cardFromApi(apiCard(3))
    expect(c.imageUrl).toBe('/api/media/3/thumb')
    expect(c.thumbUrl).toBe('/api/media/3/thumb')
    expect(c.fileUrl).toBe('/api/media/3/file')
    expect(c.tags).toEqual(['pets'])
    expect(c.tagIds).toEqual([7])
    expect(c.owned).toEqual({ foil: 1 })
    expect(c.sellPrices).toEqual({ foil: 30 })
    expect(c.reviewAvailable).toBe(true)
    expect(c.upgradeCost).toBe(50)
    expect(c.copies).toBe(2)
    expect(c.reviewReward).toBeNull()
    expect(cardFromApi(apiCard(5, { review_reward: 25 })).reviewReward).toBe(25)
    expect(cardFromApi(apiCard(4, { thumb_url: null, kind: 'video' })).imageUrl).toBe('')
  })

  it('ApiError joins FastAPI array-style 422 detail messages', () => {
    const e = new ApiError(422, [{ msg: 'String should have at least 1 character', loc: ['body', 'name'] }, { msg: 'second' }])
    expect(e.message).toBe('String should have at least 1 character; second')
    expect(new ApiError(422, [{ nope: 1 }]).message).toBe('Request failed (422)')
  })

  it('openPackApi sends Idempotency-Key and maps is_new/near_miss', async () => {
    const fetchMock = vi.fn(() =>
      json({
        pack_id: 'standard', cost: 210, coins: 990, replayed: false,
        pulls: [{ media_id: 1, treatment: 'foil', outcome: 'new', near_miss: true, coins: 0, upgraded_to: null, card: apiCard(1), is_new: true }],
      }))
    vi.stubGlobal('fetch', fetchMock)
    const pulls = await openPackApi('standard', 'key-1')
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/packs/standard/open')
    expect(init.method).toBe('POST')
    expect((init.headers as Record<string, string>)['Idempotency-Key']).toBe('key-1')
    expect(pulls).toHaveLength(1)
    expect(pulls[0].isNew).toBe(true)
    expect(pulls[0].nearMiss).toBe(true)
    expect(pulls[0].card.id).toBe(1)
  })

  it('openPackApi shows the copy you pulled, not your best copy', async () => {
    const best = apiCard(1, { treatment: 'special', owned: { normal: true, special: true } })
    vi.stubGlobal('fetch', vi.fn(() =>
      json({
        pack_id: 'standard', cost: 210, coins: 990, replayed: false,
        pulls: [
          { media_id: 1, treatment: 'normal', outcome: 'duplicate', near_miss: false, coins: 3, upgraded_to: null, card: best, is_new: false },
          { media_id: 1, treatment: 'normal', outcome: 'upgraded', near_miss: false, coins: 0, upgraded_to: 'foil', card: best, is_new: false },
        ],
      })))
    const pulls = await openPackApi('standard', 'key-2')
    expect(pulls[0].card.treatment).toBe('normal')
    expect(pulls[1].card.treatment).toBe('foil')
    expect(pulls[0].collectionCard?.treatment).toBe('special')
  })

  it('ApiError carries status and detail on 402', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({ detail: { message: 'Not enough coins', needed: 210, balance: 5 } }, 402)))
    const err = await openPackApi('standard', 'k').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect(err.status).toBe(402)
    expect(err.detail.message).toBe('Not enough coins')
    expect(err.message).toBe('Not enough coins')
  })
})

function liveFetch(total: number, onOpen?: () => Promise<Response>) {
  let coins = 777
  return vi.fn((url: string) => {
    if (url.startsWith('/api/media?limit=1')) return json({ total, items: [] })
    if (url.startsWith('/api/media?')) return json({ total, items: Array.from({ length: total }, (_, i) => apiCard(i + 1)) })
    if (url === '/api/economy/wallet') return json({ coins, packs_opened: 4 })
    if (url.includes('/open')) { coins = 567; return onOpen ? onOpen() : json({ pack_id: 'standard', cost: 210, coins: 567, replayed: false, pulls: [] }) }
    return json({}, 404)
  })
}

describe('store live mode', () => {
  it('an empty folder means no cards at all, and the real balance (no sample sandbox)', async () => {
    vi.stubGlobal('fetch', liveFetch(0))
    await initData()
    expect(getState().mode).toBe('empty')
    expect(getState().cards).toEqual([])
    expect(getState().wallet.coins).toBe(777)
  })

  it('uses the live pack price from the wallet for cost and afford count', async () => {
    const f = vi.fn((url: string) => {
      if (url.startsWith('/api/media?limit=1')) return json({ total: 0, items: [] })
      if (url === '/api/economy/wallet') return json({ coins: 440, packs_opened: 13, pack_prices: { standard: 225 } })
      return json({}, 404)
    })
    vi.stubGlobal('fetch', f)
    await initData()
    const std = { id: 'standard', name: 'Standard', blurb: '', cost: 210, size: 7, hue: 1, odds: {} }
    expect(packCost(std)).toBe(225)
    expect(affordable(std)).toBe(1) // 440 buys two at the base price but only one at today's
    expect(packCost({ ...std, id: 'other' })).toBe(210)
  })

  it('initData picks live when media exist', async () => {
    vi.stubGlobal('fetch', liveFetch(3))
    await initData()
    const s = getState()
    expect(s.mode).toBe('live')
    expect(s.cards).toHaveLength(3)
    expect(s.wallet.coins).toBe(777)
    expect(s.wallet.packsOpened).toBe(4)
  })

  it('openPack ignores a second call while one is in flight', async () => {
    const fetchMock = liveFetch(3)
    vi.stubGlobal('fetch', fetchMock)
    await initData()
    fetchMock.mockClear()
    const [a, b] = await Promise.all([openPack('standard'), openPack('standard')])
    expect(a).toEqual([])
    expect(b).toBeNull()
    expect(fetchMock.mock.calls.filter((c) => String(c[0]).includes('/open'))).toHaveLength(1)
    expect(getState().wallet.coins).toBe(567)
  })

  it('openPack rethrows a 402 ApiError and frees the in-flight flag', async () => {
    const fetchMock = liveFetch(3, () => json({ detail: { message: 'Not enough coins' } }, 402))
    vi.stubGlobal('fetch', fetchMock)
    await initData()
    await expect(openPack('standard', true)).rejects.toBeInstanceOf(ApiError)
    fetchMock.mockImplementation((() => json({ pack_id: 'standard', cost: 1, coins: 1, replayed: false, pulls: [] })) as never)
    await expect(openPack('standard')).resolves.toEqual([])
  })

  it('openPack only buys when asked to', async () => {
    const fetchMock = liveFetch(3)
    vi.stubGlobal('fetch', fetchMock)
    await initData()
    fetchMock.mockClear()
    await openPack('standard')
    await openPack('standard', true)
    const urls = fetchMock.mock.calls.map((c) => String(c[0])).filter((u) => u.includes('/open'))
    expect(urls).toEqual(['/api/packs/standard/open', '/api/packs/standard/open?buy=true'])
  })

  it('buying puts sealed packs in the inventory without opening them', async () => {
    const f = vi.fn((url: string) => {
      if (url.startsWith('/api/media?limit=1')) return json({ total: 3, items: [] })
      if (url.startsWith('/api/media?')) return json({ total: 0, items: [] })
      if (url === '/api/economy/wallet') return json({ coins: 300, packs_opened: 2, inventory: { standard: 2 }, cards_total: 3, cards_owned: 0, cards_tagged: 0 })
      if (url.includes('/buy')) return json({ pack_id: 'standard', bought: 2, cost: 200, coins: 300, inventory: { standard: 2 }, replayed: false })
      return json({}, 404)
    })
    vi.stubGlobal('fetch', f)
    await initData()
    const r = await buyPacks('standard', 2)
    expect(r?.bought).toBe(2)
    expect(sealedTotal()).toBe(2)
    expect(getState().cards).toEqual([]) // nothing was pulled
    const [url, init] = f.mock.calls.find((c) => String(c[0]).includes('/buy')) as unknown as [string, RequestInit]
    expect(url).toBe('/api/packs/standard/buy')
    expect(JSON.parse(String(init.body))).toEqual({ count: 2, deal: false })
  })

  it('loads only the cards you pulled, and counts the rest from the wallet', async () => {
    const f = vi.fn((url: string) => {
      if (url.startsWith('/api/media?limit=1')) return json({ total: 1126, items: [] })
      if (url.startsWith('/api/media?filter=pulled')) return json({ total: 2, items: [apiCard(1), apiCard(2)] })
      if (url === '/api/economy/wallet') return json({ coins: 5, packs_opened: 1, completeness: 0.001, slots_filled: 3, slots_total: 3378, cards_total: 1126, cards_owned: 2, cards_tagged: 1 })
      return json({}, 404)
    })
    vi.stubGlobal('fetch', f)
    await initData()
    expect(getState().cards).toHaveLength(2)
    expect(f.mock.calls.some((c) => /\/api\/media\?(?!limit=1)(?!filter=pulled)/.test(String(c[0])))).toBe(false) // never the whole library
    expect(completeness()).toEqual({ total: 1126, owned: 2, tagged: 1, pulled: 2 / 1126, ratio: 0.5, complete: 0.001, versions: 3, versionsTotal: 3378 })
    expect(versionsText(completeness())).toBe(`3 of ${(3378).toLocaleString()} versions`)
  })

  it('shows a small share with one decimal', () => {
    expect([0, 0.001, 0.0949, 0.0996, 0.333, 1].map(pctText)).toEqual(['0%', '0.1%', '9.5%', '10%', '33%', '100%'])
  })
})

describe('initData failure handling', () => {
  it('keeps live state when fetch rejects', async () => {
    vi.stubGlobal('fetch', liveFetch(3))
    await initData()
    const cards = getState().cards
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('network'))))
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    await initData()
    expect(getState().mode).toBe('live')
    expect(getState().cards).toBe(cards)
    expect(getState().wallet.coins).toBe(777)
  })

  it('goes live -> empty on a successful total 0', async () => {
    vi.stubGlobal('fetch', liveFetch(3))
    await initData()
    vi.stubGlobal('fetch', liveFetch(0))
    await initData()
    expect(getState().mode).toBe('empty')
  })
})

describe('shouldReload', () => {
  const st = (state: 'idle' | 'scanning', last: Record<string, unknown> | null): ScanStatus => ({ state, done: 0, total: 0, last_report: last })
  it('true when a scan just ended', () => expect(shouldReload(st('scanning', null), st('idle', { n: 1 }))).toBe(true))
  it('true idle->idle with a changed last_report', () => expect(shouldReload(st('idle', { n: 1 }), st('idle', { n: 2 }))).toBe(true))
  it('false when nothing changed, first poll, or still scanning', () => {
    expect(shouldReload(st('idle', { n: 1 }), st('idle', { n: 1 }))).toBe(false)
    expect(shouldReload(null, st('idle', { n: 1 }))).toBe(false)
    expect(shouldReload(st('idle', null), st('scanning', null))).toBe(false)
  })
})

describe('fetchAllCards', () => {
  it('keeps newest-first order and drops ids repeated across pages (rows inserted while paging)', async () => {
    const pages = [
      { total: 3, items: [apiCard(1), apiCard(2)] },
      { total: 4, items: [apiCard(2), apiCard(3), apiCard(4)] }, // a new row shifted card 2 onto page 2
    ]
    const f = vi.fn((url: string) => {
      expect(url).toContain('sort=added')
      return json(pages.shift() ?? { total: 4, items: [] })
    })
    vi.stubGlobal('fetch', f)
    const cards = await fetchAllCards()
    expect(cards.map((c) => c.id)).toEqual([1, 2, 3, 4])
  })
})
