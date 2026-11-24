import { describe, expect, it } from 'vitest'
import { makeTestCard } from '../data/testCards.ts'
import type { CardHistory } from '../data/marketApi.ts'
import { countdownText } from '../ui/countdown.ts'
import { chartStats, priceChartHTML, priceChartPanelHTML, sliceRange } from '../ui/priceChart.ts'
import {
  offerVerdict, ownedTreatments, printText, sortByValue, staleMarket, trendText, wantedBonusText,
} from './market.ts'

const NOW = Date.parse('2026-11-14T12:00:00Z')
const at = (ms: number) => new Date(NOW + ms).toISOString()

describe('countdownText', () => {
  it('shows hours and minutes', () => expect(countdownText(at((2 * 60 + 13) * 60_000 + 20_000), NOW)).toBe('2 h 13 min'))
  it('shows minutes and seconds under an hour', () => expect(countdownText(at(4 * 60_000 + 5_000), NOW)).toBe('4 min 05 s'))
  it('shows seconds under a minute', () => expect(countdownText(at(9_000), NOW)).toBe('9 s'))
  it('says now when past, zero, null or invalid', () => {
    expect(countdownText(at(-5000), NOW)).toBe('now')
    expect(countdownText(at(0), NOW)).toBe('now')
    expect(countdownText(null, NOW)).toBe('now')
    expect(countdownText('nonsense', NOW)).toBe('now')
  })
})

const pts = (prices: number[]) => prices.map((price, i) => ({ at: at(i * 6 * 3600_000), price }))

describe('sliceRange', () => {
  const many = Array.from({ length: 200 }, (_, i) => i)
  it('keeps hours / tick + 1 points', () => {
    expect(sliceRange(many, 'day', 6)).toHaveLength(5)
    expect(sliceRange(many, 'week', 6)).toHaveLength(29)
    expect(sliceRange(many, 'month', 6)).toHaveLength(121) // 30 days, the server's own month
  })
  it('takes the newest points', () => expect(sliceRange(many, 'day', 6).at(-1)).toBe(199))
  it('never returns more than there are', () => expect(sliceRange([1, 2, 3], 'month', 6)).toEqual([1, 2, 3]))
})

describe('chartStats', () => {
  it('finds min, max, first, last and change', () => {
    const s = chartStats(pts([100, 80, 120, 110]))
    expect(s).toMatchObject({ min: 80, max: 120, first: 100, last: 110 })
    expect(s.change).toBeCloseTo(0.1, 6)
  })
  it('has no change with fewer than two points', () => {
    expect(chartStats(pts([50])).change).toBe(0)
    expect(chartStats([]).change).toBe(0)
  })
})

describe('priceChartHTML', () => {
  it('asks for more history when there are fewer than two points', () => {
    const html = priceChartHTML(pts([5]))
    expect(html).toContain('Not enough history yet')
    expect(html).not.toContain('<svg')
  })
  it('draws a green line for a rise and a red one for a fall', () => {
    expect(priceChartHTML(pts([10, 12]))).toContain('pc-up')
    expect(priceChartHTML(pts([12, 10]))).toContain('pc-down')
  })
  it('labels min and max, scaled by the multiplier', () => {
    const html = priceChartHTML(pts([100, 200]), { multiplier: 3 })
    expect(html).toContain('>300<')
    expect(html).toContain('>600<')
    expect(html).toContain('viewBox')
  })
  it('escapes dates it cannot read', () => {
    const html = priceChartHTML([{ at: '<b>x</b>', price: 1 }, { at: '"><script>', price: 2 }])
    expect(html).not.toContain('<b>x</b>')
    expect(html).not.toContain('<script>')
    expect(html).toContain('&#60;b&#62;')
  })
})

describe('priceChartPanelHTML', () => {
  const history: CardHistory = {
    media_id: 1, tick_hours: 6, points: pts([100, 110, 120]), treatment_value: { normal: 1, foil: 2 },
    next_tick_at: at(0), value: 120, print_run: 50, print_tier: 'Chase', trend: 0.1, wanted: false,
  }
  it('shows the price for the treatment and marks the pressed range', () => {
    const html = priceChartPanelHTML(history, 'week', 'foil')
    expect(html).toContain('<b>240</b>')
    expect(html).toContain('data-range="week" aria-pressed="true"')
    expect(html).toContain('data-range="day" aria-pressed="false"')
    expect(html).toContain('One point every 6 hours')
    expect(html).toContain('▲ 20%')
  })
})

describe('trendText', () => {
  it('formats up, down and flat', () => {
    expect(trendText(0.12)).toBe('▲ 12%')
    expect(trendText(-0.03)).toBe('▼ 3%')
    expect(trendText(0)).toBe('– 0%')
    expect(trendText(0.001)).toBe('– 0%')
  })
})

describe('offerVerdict', () => {
  it('is above the market', () => expect(offerVerdict(108, 100)).toEqual({ above: true, pct: 8, text: '8% above the market' }))
  it('is below the market', () => expect(offerVerdict(95, 100)).toEqual({ above: false, pct: 5, text: '5% below the market' }))
  it('is level with the market', () => expect(offerVerdict(100, 100).text).toBe('Right at the market price'))
  it('copes with a zero market price', () => expect(offerVerdict(10, 0).pct).toBe(0))
})

describe('sortByValue', () => {
  it('puts the most valuable first and leaves the input alone', () => {
    const cards = [makeTestCard({ id: 1, value: 5 }), makeTestCard({ id: 2, value: 50 }), makeTestCard({ id: 3, value: 20 })]
    expect(sortByValue(cards).map((c) => c.id)).toEqual([2, 3, 1])
    expect(cards.map((c) => c.id)).toEqual([1, 2, 3])
  })
})

describe('printText', () => {
  it('joins the tier and the run', () => expect(printText('Chase', 62)).toBe('Chase · 62 printed'))
  it('copes with missing parts', () => {
    expect(printText('', 62)).toBe('62 printed')
    expect(printText('Chase', 0)).toBe('Chase')
  })
})

describe('other helpers', () => {
  it('wantedBonusText reads a multiplier or a fraction', () => {
    expect(wantedBonusText(1.5)).toBe('+50%')
    expect(wantedBonusText(0.5)).toBe('+50%')
  })
  it('ownedTreatments lists owned treatments, best first', () => {
    const card = makeTestCard({ copiesByTreatment: { normal: 2, holo: 1, foil: 0 } })
    expect(ownedTreatments(card, ['normal', 'foil', 'holo', 'special'])).toEqual(['holo', 'normal'])
  })
})

describe('v0.7.1: the Market follows the price update (B1) and the wanted bonus (L2)', () => {
  it('knows when its data is of another price update than the wallet', () => {
    expect(staleMarket(7, 8)).toBe(true)
    expect(staleMarket(8, 8)).toBe(false)
    expect(staleMarket(7, 0)).toBe(false) // the wallet is not loaded yet
  })
  it('shows the bonus the server sends', () => {
    expect(wantedBonusText(1.3)).toBe('+30%')
  })
})
