/** A small dependency-free SVG line chart for prices, and the panel that wraps it (header + range buttons). */

import './priceChart.css'
import { esc } from '../cards/card.ts'
import type { CardHistory, PricePoint } from '../data/marketApi.ts'

export type Range = 'day' | 'week' | 'month'

/** The ranges are the server's own (game/market.py `changes`): 24 hours, 7 days, 30 days. */
export const RANGE_HOURS: Record<Range, number> = { day: 24, week: 24 * 7, month: 24 * 30 }
const RANGE_LABEL: Record<Range, string> = { day: '24 h', week: 'Week', month: 'Month' }
const RANGE_PHRASE: Record<Range, string> = { day: 'in 24 h', week: 'this week', month: 'this month' }

/**
 * A price change with the range it covers: "▼ 3% in 24 h", "▲ 12% this month". The number and
 * the chart of that range come from the same prices, so they always agree. '' when it rounds to 0%
 * (`sayFlat`: "no change in 24 h" instead).
 */
export function rangeChangeText(change: number, range: Range, sayFlat = false): string {
  const pct = Math.round(Math.abs(change) * 100)
  if (pct === 0) return sayFlat ? `no change ${RANGE_PHRASE[range]}` : ''
  return `${change > 0 ? '▲' : '▼'} ${pct}% ${RANGE_PHRASE[range]}`
}

/** The last points that cover the range (one point per tick), never more than there are. */
export function sliceRange<T>(points: T[], range: Range, tickHours: number): T[] {
  const per = tickHours > 0 ? tickHours : 6
  const n = Math.floor(RANGE_HOURS[range] / per) + 1
  return points.length <= n ? points.slice() : points.slice(points.length - n)
}

export interface ChartStats { min: number; max: number; first: number; last: number; change: number }

export function chartStats(points: PricePoint[]): ChartStats {
  if (!points.length) return { min: 0, max: 0, first: 0, last: 0, change: 0 }
  const prices = points.map((p) => p.price)
  const first = prices[0]
  const last = prices[prices.length - 1]
  return {
    min: Math.min(...prices),
    max: Math.max(...prices),
    first,
    last,
    change: points.length >= 2 && first ? last / first - 1 : 0,
  }
}

/** A short local date and time, e.g. "Nov 14, 06:00". Unreadable input is returned escaped as it came. */
export function shortDate(iso: string): string {
  const t = Date.parse(iso)
  if (!Number.isFinite(t)) return esc(iso)
  return esc(new Date(t).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }))
}

const num = (n: number) => Math.round(n).toLocaleString('en-US')

export interface ChartOptions { width?: number; height?: number; multiplier?: number }

export function priceChartHTML(points: PricePoint[], opts: ChartOptions = {}): string {
  if (points.length < 2) return '<p class="pc-empty muted">Not enough history yet.</p>'
  const W = opts.width ?? 600
  const H = opts.height ?? 160
  const k = opts.multiplier ?? 1
  const padL = 46
  const padR = 10
  const padT = 14
  const padB = 22
  const s = chartStats(points)
  const lo = s.min * k
  const hi = s.max * k
  const span = hi - lo || 1
  // Axis labels: whole coins for prices, two decimals for small numbers such as the market index (1.37).
  const small = hi < 10 && !Number.isInteger(hi)
  const axis = (v: number) => (small ? v.toFixed(2) : num(v))
  const x = (i: number) => padL + (i / (points.length - 1)) * (W - padL - padR)
  const y = (p: number) => padT + (1 - (p * k - lo) / span) * (H - padT - padB)
  const pts = points.map((p, i) => `${x(i).toFixed(1)},${y(p.price).toFixed(1)}`)
  const line = pts.join(' ')
  const base = (H - padB).toFixed(1)
  const area = `${x(0).toFixed(1)},${base} ${line} ${x(points.length - 1).toFixed(1)},${base}`
  const dir = s.change >= 0 ? 'pc-up' : 'pc-down'
  const lx = x(points.length - 1).toFixed(1)
  const ly = y(points[points.length - 1].price).toFixed(1)
  return `
  <svg class="pc ${dir}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Price history, from ${num(s.first * k)} to ${num(s.last * k)} coins">
    <line class="pc-grid" x1="${padL}" x2="${W - padR}" y1="${padT}" y2="${padT}"/>
    <line class="pc-grid" x1="${padL}" x2="${W - padR}" y1="${base}" y2="${base}"/>
    <polygon class="pc-area" points="${area}"/>
    <polyline class="pc-line" points="${line}" fill="none"/>
    <circle class="pc-dot" cx="${lx}" cy="${ly}" r="3.5"/>
    <text class="pc-label" x="${padL - 6}" y="${padT + 4}" text-anchor="end">${axis(hi)}</text>
    <text class="pc-label" x="${padL - 6}" y="${H - padB}" text-anchor="end">${axis(lo)}</text>
    <text class="pc-date" x="${padL}" y="${H - 6}" text-anchor="start">${shortDate(points[0].at)}</text>
    <text class="pc-date" x="${W - padR}" y="${H - 6}" text-anchor="end">${shortDate(points[points.length - 1].at)}</text>
  </svg>`
}

/** The chart for one card with its header and the Day / Week / Month buttons. The caller handles the clicks (`[data-range]`). */
export function priceChartPanelHTML(history: CardHistory, range: Range, treatment: string, chart: ChartOptions = {}): string {
  const k = history.treatment_value[treatment] ?? 1
  const points = sliceRange(history.points, range, history.tick_hours)
  const s = chartStats(points)
  const now = Math.round(history.value * k)
  // The server's number for this range when it sends one; it is the change between the chart's first and last point.
  const change = history.changes?.[range] ?? s.change
  const up = change >= 0
  return `
  <div class="pc-panel">
    <div class="pc-head">
      <span class="pc-price">🪙 <b>${now.toLocaleString('en-US')}</b></span>
      ${points.length >= 2 ? `<span class="pc-change ${Math.round(Math.abs(change) * 100) === 0 ? 'muted' : up ? 'is-up' : 'is-down'}">${rangeChangeText(change, range, true)}</span>` : ''}
      <span class="muted pc-note">One point every ${history.tick_hours} hours</span>
      <span class="pc-ranges" role="group" aria-label="Time range">
        ${(Object.keys(RANGE_LABEL) as Range[]).map((r) =>
          `<button type="button" class="pc-range" data-range="${r}" aria-pressed="${r === range}">${RANGE_LABEL[r]}</button>`).join('')}
      </span>
    </div>
    ${priceChartHTML(points, { ...chart, multiplier: k })}
  </div>`
}
