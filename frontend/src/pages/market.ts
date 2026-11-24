/**
 * Market: what your collection is worth and how it moved, your cards that moved most, the cards
 * collectors want, the cards for sale (four lists, one per treatment: the 10 most valuable Normal,
 * Foil, Holo and Special cards), your own cards with a sell button per treatment, and the news.
 * The server owns every price; this page only shows them and sends trades. After a trade the
 * Market stays: the lists are loaded again and the version pill shows what changed.
 */

import './market.css'
import { cardHTML, enableTilt, esc } from '../cards/card.ts'
import { visualFor } from '../cards/treatments.ts'
import { ApiError, sellCard } from '../data/api.ts'
import {
  buyListed, fetchMarket, newsCards, sellAllDuplicates, type Listing, type Market, type NewsItem,
} from '../data/marketApi.ts'
import { applySlots, getState, reloadAll, subscribe } from '../data/store.ts'
import type { Card } from '../data/types.ts'
import { floatCoins } from '../fx/effects.ts'
import { play } from '../fx/sound.ts'
import { countdownText, startCountdowns, tickClockTimes } from '../ui/countdown.ts'
import { valueChangeText } from '../app/shell.ts'
import { priceChartHTML, shortDate } from '../ui/priceChart.ts'
import { confirmDialog, duplicatesDialog, netVersionsText, saleDialog } from '../ui/confirm.ts'
import { newsNamesHTML } from '../ui/newsNames.ts'
import { toast, toastMilestones } from '../ui/toast.ts'
import type { Page } from '../app/router.ts'
import { firstVisitTour } from '../help/index.ts'
import { openInspect, wantedText } from './inspect.ts'

/** Cards in "Your cards" per page. */
export const ROWS_PER_PAGE = 60

/** What one list of the market says when it is short or empty: only cards worth enough are traded. */
export function listNote(rows: number, max: number, treatment: string, minValue: number): string {
  if (rows >= max) return ''
  const why = `Only cards whose Normal copy is worth ${minValue} coins or more are traded.`
  return rows === 0 ? `No ${treatment} card is for sale right now. ${why}` : `${rows} of ${max}: a short list. ${why}`
}

const coins = (n: number) => `🪙 ${Math.round(n).toLocaleString('en-US')}`

/** "▲ 12%", "▼ 3%" or "– 0%" for a price change given as a fraction. */
export function trendText(trend: number): string {
  const pct = Math.round(Math.abs(trend) * 100)
  if (pct === 0) return '– 0%'
  return `${trend > 0 ? '▲' : '▼'} ${pct}%`
}

/** How a collector's price compares with the market price, in plain words. */
export function offerVerdict(price: number, marketPrice: number): { above: boolean; pct: number; text: string } {
  const pct = marketPrice > 0 ? Math.round(Math.abs(price / marketPrice - 1) * 100) : 0
  const above = price >= marketPrice
  const text = pct === 0 ? 'Right at the market price' : `${pct}% ${above ? 'above' : 'below'} the market`
  return { above, pct, text }
}

/** The cards, most valuable first (a new array; ties keep their order). */
export function sortByValue(cards: Card[]): Card[] {
  return cards.slice().sort((a, b) => b.value - a.value)
}

/** "Chase · 62 printed". */
export function printText(tier: string, run: number): string {
  if (run > 0) return tier ? `${tier} · ${run.toLocaleString('en-US')} printed` : `${run.toLocaleString('en-US')} printed`
  return tier
}

/** The extra a collector pays, as "+50%". The server sends the multiplier (1.5); a plain fraction (0.5) works too. */
export function wantedBonusText(bonus: number): string {
  const frac = bonus > 1 ? bonus - 1 : bonus
  return `+${Math.round(frac * 100)}%`
}

/** The treatments of a card that you own, best first. `order` lists treatment ids from lowest to highest. */
export function ownedTreatments(card: Card, order: string[]): string[] {
  const rank = (t: string) => { const i = order.indexOf(t); return i < 0 ? -1 : i }
  return Object.entries(card.copiesByTreatment).filter(([, n]) => n > 0).map(([t]) => t).sort((a, b) => rank(b) - rank(a))
}

/** The page's data is of another price update than the store's wallet (0 = the wallet is not loaded yet). */
export function staleMarket(pageTick: number, walletTick: number): boolean {
  return walletTick !== 0 && pageTick !== walletTick
}

const sellLabel = (t: string) => visualFor(t).label

function errorText(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.status === 402) return 'You do not have enough coins for that.'
    if (err.status === 409) return err.message || fallback
  }
  return err instanceof Error && err.message ? err.message : fallback
}

export const marketPage: Page = {
  id: 'market',
  title: 'Market',
  icon: '⚖',
  mount(el) {
    let data: Market | null = null
    let error = ''
    let busy = false
    let dead = false
    let shown = ROWS_PER_PAGE
    let loadId = 0

    const order = () => getState().treatments.slice().sort((a, b) => a.tier - b.tier).map((t) => t.id)

    // ---- pieces ----

    function headerHTML(m: Market): string {
      const value = getState().wallet.collectionValue || m.collection_value
      const ch = m.collection_change
      const times = tickClockTimes(m.next_tick_at, m.tick_hours)
      return `
      <section class="panel mk-head" data-tour="market-head">
        <div class="mk-stats">
          <div class="mk-stat"><span class="muted">Your collection is worth</span><b class="mk-big mk-coin">${coins(value)}</b></div>
          <div class="mk-stat"><span class="muted">At the last update</span>
            <b class="mk-big ${ch.change > 0 ? 'is-up' : ch.change < 0 ? 'is-down' : ''}">${valueChangeText(ch.change)}${ch.coins ? ` <small>${ch.coins > 0 ? '+' : '−'}${Math.abs(ch.coins).toLocaleString('en-US')} coins</small>` : ''}</b></div>
          <div class="mk-stat"><span class="muted">Next update in</span>
            <b class="mk-big" data-countdown="${esc(m.next_tick_at)}">${countdownText(m.next_tick_at)}</b></div>
        </div>
        <p class="muted mk-when">Every card has its own price. All prices change at the same moment, every ${m.tick_hours} hours${times ? `: at ${esc(times)}` : ''}.
          Your own trades do not move a price: you are one collector among millions. This value counts the cards you hold now, so it also changes when you open packs, sell or buy.</p>
        <figure class="mk-index-chart">
          ${priceChartHTML(m.collection_history, { height: 120 })}
          <figcaption class="muted">What the cards you hold now were worth over the last month</figcaption>
        </figure>
      </section>`
    }

    function moversHTML(m: Market): string {
      if (!m.movers.length) return ''
      return `
      <section class="panel" data-tour="market-movers">
        <h2>Your cards that moved most</h2>
        <div class="mk-grid">
          ${m.movers.map((v) => `
            <article class="mk-item" data-inspect="${v.card.id}" title="Open this card">
              ${cardHTML(v.card, { size: 'md' })}
              <div class="mk-meta">
                <span class="mk-trend ${v.trend >= 0 ? 'is-up' : 'is-down'}">${trendText(v.trend)} in 24 h</span>
              </div>
              <div class="mk-price">${coins(v.card.value)}</div>
            </article>`).join('')}
        </div>
      </section>`
    }

    function wantedHTML(m: Market): string {
      if (!m.wanted.length) return ''
      return `
      <section class="panel">
        <h2>Collectors want these</h2>
        <div class="mk-grid mk-grid-wide">
          ${m.wanted.map((w) => {
            const best = ownedTreatments(w.card, order())[0] ?? w.card.treatment
            const price = w.card.sellPrices[best]
            // The bonus in coins: "sells for 🪙 31 instead of 24".
            const bonus = wantedText(w.card, best)
            return `
            <article class="mk-item">
              <div class="mk-face" data-inspect="${w.card.id}" title="Open this card">${cardHTML({ ...w.card, treatment: best }, { size: 'md' })}</div>
              <p class="mk-note mk-note-wanted">${bonus ? `★ Your ${esc(sellLabel(best))} copy ${esc(bonus)}` : `${wantedBonusText(w.bonus)} when you sell it`} until the market moves</p>
              <button class="btn" type="button" data-sell="${w.card.id}" data-treatment="${esc(best)}">Sell ${esc(sellLabel(best))}${price ? ` · ${coins(price)}` : ''}</button>
            </article>`
          }).join('')}
        </div>
      </section>`
    }

    function listingHTML(l: Listing): string {
      const balance = getState().wallet.coins
      const short = l.price - balance
      const name = sellLabel(l.treatment)
      return `
      <article class="mk-item mk-listing">
        <div class="mk-face" data-inspect="${l.media_id}" title="Open this card">${cardHTML({ ...l.card, treatment: l.treatment }, { size: 'md' })}</div>
        <div class="mk-name" title="${esc(l.title)}">${esc(l.title)}</div>
        <div class="mk-price">${coins(l.price)} <span class="mk-trend ${l.trend >= 0 ? 'is-up' : 'is-down'}" title="Price change over the last 24 hours">${trendText(l.trend)}</span></div>
        <div class="mk-tier mk-tier-line" title="${esc(printText(l.print_tier, l.print_run))}">${esc(printText(l.print_tier, l.print_run))}</div>
        <p class="mk-note mk-own">${l.copies > 0 ? `You hold ${l.copies} ${esc(name)}` : `You hold no ${esc(name)}`}</p>
        <p class="mk-fills${l.fills_slot ? '' : ' is-none'}">${l.fills_slot ? `✚ Fills your ${esc(name)} slot` : ''}</p>
        <button class="btn" type="button" data-buy="${l.media_id}" data-treatment="${esc(l.treatment)}" ${short > 0 ? 'disabled' : ''}>${short > 0 ? `${short.toLocaleString('en-US')} coins short` : `Buy ${esc(name)}`}</button>
      </article>`
    }

    /** The cards for sale: four lists, one per treatment, lowest first. Cards dominate; a short list says why. */
    function listingsHTML(m: Market): string {
      const max = getState().market.listingsPerTreatment
      const min = getState().market.wantedMinValue
      const ids = [...new Set([...order(), ...Object.keys(m.listings)])]
      return `
      <section class="panel mk-sale" data-tour="market-listings">
        <h2>For sale</h2>
        <p class="muted">The ${max} most valuable cards of each treatment. The price includes the market's cut. Buying gives you one copy of that treatment, even if you never pulled the card, and a Foil, Holo or Special you do not hold fills its version slot.</p>
        ${ids.map((t) => {
          const rows = m.listings[t] ?? []
          const note = listNote(rows.length, max, sellLabel(t), min)
          return `
          <div class="mk-list" style="--rarity:${visualFor(t).color}">
            <h3 class="mk-list-title">The most valuable <b>${esc(sellLabel(t))}</b> cards for sale</h3>
            ${rows.length ? `<div class="mk-sale-grid">${rows.map(listingHTML).join('')}</div>` : ''}
            ${note ? `<p class="muted mk-list-note">${esc(note)}</p>` : ''}
          </div>`
        }).join('')}
      </section>`
    }

    function rowHTML(card: Card): string {
      const owned = ownedTreatments(card, order())
      return `
      <li class="mk-row">
        <div class="mk-row-card" data-inspect="${card.id}" title="Open this card">${cardHTML(card, { size: 'sm' })}</div>
        <div class="mk-row-info">
          <button class="mk-row-title" type="button" data-inspect="${card.id}">${esc(card.title)}</button>
          <span class="muted">${owned.map((t) => `${esc(sellLabel(t))} ×${card.copiesByTreatment[t]}`).join(' · ')}</span>
        </div>
        <div class="mk-row-value">${coins(card.value)}
          <span class="mk-trend ${card.trend >= 0 ? 'is-up' : 'is-down'}" title="Price change over the last 24 hours">${trendText(card.trend)}</span>
          ${card.wanted ? '<span class="mk-wanted" title="A collector wants this card">⭐ wanted</span>' : ''}
        </div>
        <div class="mk-row-sell">
          ${owned.map((t) => `<button class="btn btn-ghost" type="button" data-sell="${card.id}" data-treatment="${esc(t)}">Sell ${esc(sellLabel(t))} · ${coins(card.sellPrices[t] ?? 0)}</button>`).join('')}
        </div>
      </li>`
    }

    function ownCardsHTML(m: Market): string {
      const mine = sortByValue(getState().cards.filter((c) => Object.values(c.copiesByTreatment).some((n) => n > 0)))
      const d = m.duplicates
      return `
      <section class="panel" data-tour="market-mine">
        <div class="mk-title-row">
          <h2>Your cards</h2>
          ${d.copies > 0 ? `<button class="btn" type="button" data-dups="1">Sell all duplicates · ${d.copies.toLocaleString('en-US')} ${d.copies === 1 ? 'copy' : 'copies'} for ${coins(d.value)}</button>` : ''}
        </div>
        ${mine.length
          ? `<ul class="mk-rows">${mine.slice(0, shown).map(rowHTML).join('')}</ul>
             ${mine.length > shown ? `<button class="btn btn-ghost mk-more" type="button" data-more="1">Show more (${(mine.length - shown).toLocaleString('en-US')} left)</button>` : ''}`
          : '<p class="muted">You have no cards yet. Open a pack to get some.</p>'}
      </section>`
    }

    function newsHTML(items: NewsItem[]): string {
      return `
      <section class="panel" data-tour="market-news">
        <h2>Market news</h2>
        ${items.length
          ? `<ul class="mk-news">${items.map((n) => { const names = newsNamesHTML(n); return `<li class="${newsCards(n).mine ? 'is-mine' : ''}"><span class="muted">${shortDate(n.at)}</span> ${names ? `<b>${names}:</b> ` : ''}${esc(n.text)}</li>` }).join('')}</ul>`
          : '<p class="muted">Nothing has happened in the market yet. Check back after the next update.</p>'}
      </section>`
    }

    function render() {
      if (dead) return
      if (!data) {
        el.innerHTML = `<div class="mk">${error ? `<p class="mk-error">${esc(error)}</p>` : '<p class="muted">Loading the market…</p>'}</div>`
        return
      }
      const m = data
      const news = m.news.slice().sort((a, b) => Date.parse(b.at) - Date.parse(a.at))
      el.innerHTML = `
      <div class="mk">
        ${error ? `<p class="mk-error">${esc(error)}</p>` : ''}
        ${headerHTML(m)}
        ${moversHTML(m)}
        ${wantedHTML(m)}
        ${listingsHTML(m)}
        ${ownCardsHTML(m)}
        ${newsHTML(news)}
      </div>`
    }

    // ---- data ----

    async function load() {
      const id = ++loadId
      try {
        const m = await fetchMarket()
        if (dead || id !== loadId) return
        data = m
        error = ''
      } catch (err) {
        if (dead || id !== loadId) return
        error = errorText(err, 'Could not load the market. Try again in a moment.')
      }
      render()
    }

    /** One trade at a time: lock the buttons, run it, then reload everything and redraw. */
    async function trade(_btn: HTMLElement, job: () => Promise<void>) {
      if (busy) return
      busy = true
      el.querySelectorAll<HTMLButtonElement>('button').forEach((b) => { b.disabled = true })
      try {
        await job()
      } catch (err) {
        if (!dead) toast(errorText(err, 'That did not work. Try again.'), { kind: 'warn' })
      } finally {
        try { await reloadAll() } catch { /* keep what we have */ }
        busy = false
        await load()
      }
    }

    /** Sell one copy. It asks first only when the sale empties a version slot or the card leaves the collection. */
    async function sellOne(btn: HTMLElement, id: number, treatment: string) {
      const card = getState().cards.find((c) => c.id === id) ?? data?.wanted.find((w) => w.card.id === id)?.card
      const question = card ? saleDialog(card, treatment, 1) : null
      if (question && !(await confirmDialog(question))) return
      if (dead) return
      void trade(btn, async () => {
        const r = await sellCard(id, treatment, 1)
        play('coin')
        floatCoins(r.price, btn)
        applySlots(r.slots)
        toast(`Sold for ${coins(r.price)}${r.slots && r.slots.delta < 0 ? ` · ${netVersionsText(r.slots.delta)}` : ''}`, { kind: 'coin' })
      })
    }

    async function sellDuplicates(btn: HTMLElement) {
      const d = data?.duplicates
      if (!d || d.copies < 1) return
      if (!(await confirmDialog(duplicatesDialog(d.copies, d.value)))) return
      if (dead) return
      void trade(btn, async () => {
        const r = await sellAllDuplicates()
        play('coin')
        floatCoins(r.price, btn)
        applySlots(r.slots)
        toast(`Sold ${r.sold} ${r.sold === 1 ? 'copy' : 'copies'} for ${coins(r.price)}`, { kind: 'coin' })
      })
    }

    el.addEventListener('click', (e) => {
      const t = e.target as HTMLElement
      const sell = t.closest<HTMLButtonElement>('[data-sell]')
      if (sell) { if (!busy && !sell.disabled) void sellOne(sell, Number(sell.dataset.sell), sell.dataset.treatment ?? 'normal'); return }
      const buy = t.closest<HTMLButtonElement>('[data-buy]')
      if (buy) {
        if (busy || buy.disabled) return
        const treatment = buy.dataset.treatment ?? 'normal'
        const l = data?.listings[treatment]?.find((x) => x.media_id === Number(buy.dataset.buy))
        void trade(buy, async () => {
          const r = await buyListed(Number(buy.dataset.buy), treatment)
          play('coin')
          floatCoins(-r.price, buy)
          applySlots(r.slots) // the pill shows the version it filled
          toastMilestones(r.milestones)
          toast(`Bought ${l ? `${l.title} (${sellLabel(treatment)})` : 'the card'} for ${coins(r.price)}${r.slots && r.slots.delta > 0 ? ` · ${netVersionsText(r.slots.delta)}` : ''}`, { kind: 'good' })
        })
        return
      }
      const dups = t.closest<HTMLButtonElement>('[data-dups]')
      if (dups) { if (!busy && !dups.disabled) void sellDuplicates(dups); return }
      if (t.closest('[data-more]')) { shown += ROWS_PER_PAGE; render(); return }
      const insp = t.closest<HTMLElement>('[data-inspect], [data-news-card]')
      if (insp && !busy) openInspect(Number(insp.dataset.inspect ?? insp.dataset.newsCard), () => { if (!dead) void load() })
    })

    enableTilt(el)
    // The clock ran out: a new price update. The cards in the store ("Your cards") and this page's
    // own data must both be of the new update, or one card shows two prices on the same page.
    const stop = startCountdowns(el, () => { if (!busy) void reloadAll().catch(() => {}).then(load) })
    // The store follows the market by itself (data/store.ts). When its price update is not the one
    // this page was loaded with, load again.
    const off = subscribe((s) => {
      if (dead || busy || !data) return
      if (staleMarket(data.tick, s.wallet.market.tick)) void load()
    })
    render()
    void load().then(() => { if (!dead && data) void firstVisitTour('market') })

    return () => {
      dead = true
      stop()
      off()
    }
  },
}
