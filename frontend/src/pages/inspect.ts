/**
 * Card panel (display-case pattern): the large single-card view where the player organizes a card,
 * follows its price and trades its copies.
 *
 * The header names the card (with rename), its code, its value and print run. Below it the card
 * itself and two columns. Left: "Your versions" (a miniature of Normal, Foil, Holo and Special
 * with the treatment's look, what one sells for, the merge progress, Sell and Merge), then the
 * tags. Right: the price, the chart and a table of the last 30 days. On a narrow window the
 * columns stack. A click on the card or on a miniature opens that version full screen.
 *
 * A card the player does not hold (opened from the news or a market listing) is shown the same
 * way, read-only: every version says "Not owned" and where to find it.
 *
 * Coin numbers and flashes are added inside the dialog: a modal dialog sits above the page, so
 * anything added to <body> would be hidden behind it. The balance always comes from the server.
 */

import { cardHTML, enableTilt, esc } from '../cards/card.ts'
import { addTag, ApiError, fetchCard, fetchTags, fuseCard, removeTag, renameCard, reviewCard, sellCard, upgradeCard } from '../data/api.ts'
import { gainedCoins } from '../data/bulk.ts'
import { fetchCardHistory, type CardHistory } from '../data/marketApi.ts'
import { oneIn, packsHolding, perPack } from '../data/packOdds.ts'
import { applySlots, getState, refreshWallet, removeCard, replaceCard, subscribe } from '../data/store.ts'
import { visualFor } from '../cards/treatments.ts'
import type { Card, FuseOption, PackType, TreatmentInfo } from '../data/types.ts'
import { flash, floatCoins } from '../fx/effects.ts'
import { play } from '../fx/sound.ts'
import { firstVisitTour } from '../help/index.ts'
import { confirmDialog, fuseDialog, netVersionsText, saleCoins, saleDialog } from '../ui/confirm.ts'
import { openLightbox } from '../ui/lightbox.ts'
import { priceChartPanelHTML, rangeChangeText, type Range } from '../ui/priceChart.ts'
import { pauseFrom, pauseText, removedTagText } from '../ui/tagPause.ts'
import { attachTagSuggest } from '../ui/tagInput.ts'
import { toast, toastMilestones } from '../ui/toast.ts'

/** Mirrors the server rule: trim, collapse inner whitespace, lowercase, 1-40 chars. null = invalid. */
export function normalizeTagInput(s: string): string | null {
  const n = s.trim().replace(/\s+/g, ' ').toLowerCase()
  return n.length >= 1 && n.length <= 40 ? n : null
}

/** Friendly text for a failed server call (shared by the card panel, Organize and bulk tagging). */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) {
    return e.status === 429 ? 'Slow down a moment' : e.status === 402 ? 'Not enough coins' : e.message
  }
  return 'Something went wrong. Try again.'
}

/** "Premium Packs", or "Deluxe Packs and up" when dearer packs hold the card too. */
export function packsFrom(packId: string, packs: { id: string; name: string; cost: number }[] = getState().packTypes): string {
  const pack = packs.find((p) => p.id === packId)
  if (!pack) return 'the dearer packs'
  return `${pack.name}s${packs.some((p) => p.cost > pack.cost) ? ' and up' : ''}`
}

/**
 * The one thing worth doing with this card right now, or '' when nothing stands out:
 * merge when enough copies wait, sell when a collector wants it, tag when no tag is on it yet.
 */
export function bestAction(c: Card, fuseCopies: number, name: (id: string) => string = (id) => id): string {
  if (c.copies < 1) return ''
  if (c.fuseFrom) return `You hold ${c.copiesByTreatment[c.fuseFrom] ?? fuseCopies} ${name(c.fuseFrom)} copies: merge ${fuseCopies} into a rarer one.`
  if (c.wanted) return 'A collector wants this card: it sells for more until the next price update.'
  if (c.tags.length === 0 && c.nextTagReward > 0) return `No tags yet: the first one pays +${c.nextTagReward} coins.`
  return ''
}

/** "The next tag pays +5 coins", or that this card's tags are paid out. */
export function paysText(reward: number): string {
  return reward > 0 ? `The next tag pays +${reward} ${reward === 1 ? 'coin' : 'coins'}` : 'More tags on this card pay nothing. They still help you find it.'
}

const treatmentName = (id: string) => getState().treatments.find((t) => t.id === id)?.name ?? visualFor(id).label

/** How many of the card's versions (every treatment above the lowest) you hold, and how many there are. */
export function versionsHeld(card: Card, treatments: TreatmentInfo[]): { held: number; of: number } {
  const versions = [...treatments].sort((a, b) => a.tier - b.tier).slice(1)
  return { held: card.copies > 0 ? versions.filter((t) => (card.copiesByTreatment[t.id] ?? 0) > 0).length : 0, of: versions.length }
}

/**
 * What a sale names as the coins: the server's quote for selling that many copies. A sale does not
 * move the price, so it is the sale price times the count. Older servers: `sellExtras` for "all
 * but one", else the plain product.
 */
export function saleQuote(card: Pick<Card, 'sellPrices' | 'sellExtras' | 'copiesByTreatment'> & Partial<Pick<Card, 'saleQuotes'>>, treatment: string, count: number): number {
  const quoted = card.saleQuotes?.[treatment]?.[count - 1]
  if (quoted !== undefined) return quoted
  const one = card.sellPrices[treatment] ?? 0
  if (count <= 1) return one
  const extras = card.sellExtras[treatment]
  return extras !== undefined && count === (card.copiesByTreatment[treatment] ?? 0) - 1 ? extras : one * count
}

/** "▲ 4% in 24 h", "▼ 2% in 24 h" or '' when the price did not move. `trend` is the change over the last 24 hours (not since midnight, and not the last update). */
export function trendText(trend: number): string {
  return rangeChangeText(trend, 'day')
}

/**
 * The card's tags as chips that remove the tag on a click (v0.9.0): each is a button named
 * "Remove tag X", with a × that lights up when it is pointed at.
 */
export function tagChipsHTML(c: Pick<Card, 'tags' | 'tagIds'>): string {
  return c.tags.map((t, i) => `<button type="button" class="chip tag-chip tag-chip-rm" data-remove="${c.tagIds[i]}" aria-label="Remove tag ${esc(t)}" title="Remove this tag. The coins it paid are taken back.">${esc(t)}<span class="chip-x" aria-hidden="true">×</span></button>`).join('')
}

/** "sells for 🪙 31 instead of 24": what the wanted bonus is in coins, or '' when there is none. */
export function wantedText(card: Pick<Card, 'sellPrices' | 'plainPrices'>, treatment: string): string {
  const now = card.sellPrices[treatment]
  const plain = card.plainPrices[treatment]
  if (now === undefined || plain === undefined || now <= plain) return ''
  return `sells for 🪙 ${now.toLocaleString()} instead of ${plain.toLocaleString()}`
}

/** One row of "Your versions": a treatment of the card, held or not. */
export interface VersionRow {
  id: string
  name: string
  copies: number
  /** What one copy sells for now (held), and the market price of one copy (always). */
  sells: number
  market: number
  /** The merge this treatment's copies allow or still lack copies for. */
  fuse: FuseOption | null
}

/** The four versions of a card, lowest first, held or not. */
export function versionRows(card: Card, treatments: TreatmentInfo[]): VersionRow[] {
  return [...treatments].sort((a, b) => a.tier - b.tier).map((t) => ({
    id: t.id,
    name: t.name,
    copies: card.copies > 0 ? card.copiesByTreatment[t.id] ?? 0 : 0,
    sells: card.sellPrices[t.id] ?? 0,
    market: card.prices[t.id] ?? 0,
    fuse: card.fuseOptions.find((o) => o.from === t.id) ?? null,
  }))
}

/** "2 of 3 for a Foil": the merge progress with the card's own count. */
export function mergeProgressText(o: Pick<FuseOption, 'have' | 'copies' | 'to'>, name: (id: string) => string = (id) => visualFor(id).label): string {
  return `${Math.min(o.have, o.copies)} of ${o.copies} for a ${name(o.to)}`
}

/** How full the merge bar is, 0 to 100. */
export function mergeProgressPct(o: Pick<FuseOption, 'have' | 'copies'>): number {
  return o.copies > 0 ? Math.max(0, Math.min(100, (o.have / o.copies) * 100)) : 0
}

/**
 * Where a version comes from: the packs that hold this card and how often each gives that
 * treatment. "Standard Pack: 1 in 3 packs · Deluxe Pack: 53% of packs".
 */
export function findInPacksText(
  treatment: Pick<TreatmentInfo, 'id' | 'pack_odds'>, minPack: string | null,
  packs: Pick<PackType, 'id' | 'name' | 'cost' | 'odds' | 'size'>[],
): string {
  return packsHolding(minPack, packs).map((p) => `${p.name}: ${oneIn(perPack(p, treatment))}`).join(' · ')
}

/** A day of the price table: "Nov 14", the price, and "▲ 4%" / "▼ 2%" / "–". */
export interface DayRow { date: string; price: number; change: string; dir: 'up' | 'down' | 'flat' }

/** The last 30 days for the table, newest first, at a treatment's factor `k`. */
export function dayRows(days: NonNullable<CardHistory['days']>, k = 1): DayRow[] {
  return days.slice().reverse().map((d) => {
    const [y, m, day] = d.date.split('-').map(Number)
    const when = new Date(y, (m || 1) - 1, day || 1)
    const date = Number.isFinite(when.getTime()) ? when.toLocaleDateString([], { month: 'short', day: 'numeric' }) : d.date
    const c = d.change
    const pct = c === null ? 0 : Math.abs(c) * 100
    const shown = pct < 10 ? pct.toFixed(1).replace(/\.0$/, '') : String(Math.round(pct))
    const dir = c === null || pct < 0.05 ? 'flat' : c > 0 ? 'up' : 'down'
    return { date, price: Math.round(d.price * k), change: dir === 'flat' ? '–' : `${dir === 'up' ? '▲' : '▼'} ${shown}%`, dir }
  })
}

export interface InspectOptions {
  /** Open with the price column in view (the price-move badge on a thumbnail). */
  focus?: 'price'
  /**
   * The panel was opened from a view that wants the player back after a sale or a merge (the
   * Collection entered from the version pill or from Home's "Fill versions"): the panel closes
   * and this is called with what happened.
   */
  afterAction?: (text: string) => void
}

export function openInspect(id: number, onClose?: () => void, opts: InspectOptions = {}): void {
  let card = getState().cards.find((c) => c.id === id)
  const d = document.createElement('dialog')
  d.className = 'inspect'
  d.innerHTML = `
    <header class="inspect-head"></header>
    <div class="inspect-body">
      <div class="inspect-stage"></div>
      <section class="inspect-col inspect-left" aria-label="Your versions and tags"></section>
      <section class="inspect-col inspect-right" aria-label="Price" data-tour="panel-price"></section>
    </div>`
  let stageKey = ''
  let message = ''
  let busy = false
  let suggestions: string[] = []
  let renaming = false
  let range: Range = 'month'
  let priceT: string | null = null // the version whose price the right column shows; null = the best one held
  let history: CardHistory | null = null
  let historyFailed = false
  let suggest: { destroy(): void; refresh(): void } | null = null
  const part = <T extends HTMLElement>(sel: string) => d.querySelector<T>(sel)!

  const coin = (n: number) => `🪙 ${n.toLocaleString()}`

  /** The card's price change over the range the chart shows (the server's number; the same for every treatment). */
  const rangeChange = (c: Card): number => (history?.changes ?? c.changes)?.[range] ?? (range === 'day' ? c.trend : 0)

  function headHTML(c: Card): string {
    const kind = `${c.kind === 'video' ? 'Video' : 'Image'}${c.copies > 0 ? ` · pulled ×${c.copies}` : ' · not in your collection'}`
    // The change in the header is the one of the range the chart shows, and it names that range.
    const change = rangeChange(c)
    const trend = rangeChangeText(change, range)
    const title = renaming
      ? `<div class="inspect-rename">
          <input class="input" data-rename value="${esc(c.titleCustom ? c.title : '')}" placeholder="${esc(c.title)}" maxlength="60" aria-label="Name of this card" autocomplete="off">
          <button class="btn" type="button" data-act="rename-save">Save</button>
          <button class="btn btn-ghost" type="button" data-act="rename-cancel">Cancel</button>
        </div>
        <p class="muted inspect-tagnote">Only the card gets this name; the file keeps its own. Leave it empty for the automatic name.</p>`
      : `<h2 class="inspect-title"><span class="inspect-name" title="${esc(c.title)}">${esc(c.title)}</span>
          ${c.code ? `<span class="inspect-code" title="This card's permanent number">${esc(c.code)}</span>` : ''}
          ${c.copies > 0 ? '<button class="icon-btn inspect-edit" type="button" data-act="rename" title="Rename this card" aria-label="Rename this card">✎</button>' : ''}</h2>`
    const file = c.fileName && c.fileName !== c.title ? ` · file: ${esc(c.fileName)}` : ''
    return `${title}
      <p class="muted inspect-kind">${kind}${file}</p>
      <p class="inspect-value" data-tour="panel-value">
        <span class="inspect-worth" title="Market value of ${c.copies > 0 ? 'your best copy' : 'a Normal copy'}">🪙 <b>${c.value.toLocaleString()}</b></span>
        ${trend ? `<span class="${change > 0 ? 'is-up' : 'is-down'}" data-el="head-change">${trend}</span>` : ''}
        ${c.printTier ? `<span class="pill" title="How many copies of this card exist. The fewer, the scarcer: harder to pull and worth more.">${esc(c.printTier)} · ${c.printRun.toLocaleString()} printed</span>` : ''}
        ${c.minPack ? `<span class="pill pill-wanted" title="This card is too scarce for the cheaper packs">★ Only in ${esc(packsFrom(c.minPack))}</span>` : ''}
        ${c.wanted ? '<span class="pill pill-wanted" title="A collector pays extra for this card until the market moves again">★ Wanted</span>' : ''}
      </p>
      <p class="inspect-msg" role="status" aria-live="polite">${esc(message)}</p>
      <button class="icon-btn inspect-x" type="button" data-act="close" aria-label="Close" title="Close (Esc)">✕</button>`
  }

  function versionsHTML(c: Card): string {
    const s = getState()
    const rows = versionRows(c, s.treatments)
    const findPack = c.minPack ?? [...s.packTypes].sort((a, b) => a.cost - b.cost)[0]?.id ?? ''
    const dear = c.fuseOptions.find((o) => o.copies > 3)
    const base = s.wallet.fuseCopies
    return `<div class="inspect-versions" data-tour="panel-versions">
      <h3>Your versions</h3>
      ${rows.map((r) => {
        const held = r.copies > 0
        const t = s.treatments.find((x) => x.id === r.id)!
        const wanted = held ? wantedText(c, r.id) : ''
        const extras = r.copies - 1
        return `<div class="ver-row ${held ? 'is-held' : 'is-missing'}" style="--rarity:${visualFor(r.id).color}">
          <div class="ver-mini" data-version="${esc(r.id)}" title="See the ${esc(r.name)} version full screen${held ? '' : ' (a preview: you do not own it)'}">
            ${cardHTML({ ...c, treatment: r.id }, { size: 'sm', ring: false })}
            ${held ? '' : '<span class="ver-mini-mark" aria-hidden="true">Not owned</span>'}
          </div>
          <div class="ver-info">
            <div class="ver-name"><b>${esc(r.name)}</b>${held ? ` <span class="ver-count">×${r.copies}</span>` : ` <button type="button" class="ver-none ver-preview" data-version="${esc(r.id)}" title="See the ${esc(r.name)} version full screen (a preview: you do not own it)">Not owned · preview</button>`}</div>
            ${held
              ? `<div class="ver-price">${wanted ? `★ Wanted: ${esc(wanted)}` : `Sells for ${coin(r.sells)}${r.copies > 1 ? ' each' : ''}`}</div>
                 ${r.fuse ? `<div class="ver-merge" title="${r.fuse.copies} ${esc(r.name)} copies merge into one ${esc(treatmentName(r.fuse.to))}">
                   <span>${esc(mergeProgressText(r.fuse, treatmentName))}</span>
                   <span class="ver-bar${r.fuse.can ? ' is-full' : ''}" role="img" aria-label="${esc(mergeProgressText(r.fuse, treatmentName))}"><i style="width:${mergeProgressPct(r.fuse)}%"></i></span></div>` : ''}`
              : `<div class="ver-price muted">Worth ${coin(r.market)} on the market</div>
                 <div class="ver-find muted">${esc(findInPacksText(t, c.minPack, s.packTypes))}</div>`}
          </div>
          <div class="ver-acts">
            ${held ? `<button class="btn btn-ghost" type="button" data-sell="${esc(r.id)}" data-count="1">Sell 1 · ${coin(saleCoins(c, r.id, 1))}</button>
              ${extras > 1 ? `<button class="btn btn-ghost" type="button" data-sell="${esc(r.id)}" data-count="${extras}" title="Keeps one copy">Sell ${extras} extras · ${coin(saleCoins(c, r.id, extras))}</button>` : ''}
              ${r.fuse?.can ? `<button class="btn" type="button" data-fuse="${esc(r.id)}" title="Gives up ${r.fuse.copies} ${esc(r.name)} copies for one ${esc(treatmentName(r.fuse.to))} copy">Merge ${r.fuse.copies} → ${esc(treatmentName(r.fuse.to))}</button>` : ''}`
              : `<a class="btn btn-ghost" href="#/packs?pack=${esc(findPack)}" title="To the pack shop">Find in packs</a>`}
          </div>
        </div>`
      }).join('')}
      <p class="muted inspect-tagnote">Selling pays ${Math.round(s.market.sellRate * 100)}% of a copy's price. Merging gives up copies of one treatment for one copy of the next.${dear ? ` Dearer cards take more copies: this card needs ${dear.copies} per merge, not the usual ${base}. The count follows its market price.` : ''}</p>
    </div>`
  }

  function tagsHTML(c: Card): string {
    if (c.copies < 1) {
      return `<div class="inspect-unowned"><h3>Not in your collection</h3>
        <p class="muted">You do not hold a copy of this card. It comes out of packs; the most valuable cards are also for sale on the market.</p>
        <p><a class="btn" href="#/packs">Open packs</a> <a class="btn btn-ghost" href="#/market">See the market</a></p></div>`
    }
    const chips = c.tags.length ? tagChipsHTML(c) : '<span class="muted">No tags yet</span>'
    const chance = Math.round(getState().upgrades.chance * 100)
    return `<div data-tour="panel-tags">
        <h3>Tags</h3>
        <div class="inspect-tags">${chips}</div>
        <p class="inspect-pays">${esc(paysText(c.nextTagReward))}</p>
        <div class="inspect-add">
          <div class="inspect-tagwrap"><input class="input" data-input="tag" placeholder="Add a tag (Enter)" maxlength="60" aria-label="Add a tag" autocomplete="off"></div>
          <input class="input" data-input="parent" list="inspect-tag-list" placeholder="Category (optional)" maxlength="60" aria-label="Category (optional)" autocomplete="off">
          <datalist id="inspect-tag-list">${suggestions.map((s) => `<option value="${esc(s)}"></option>`).join('')}</datalist>
        </div>
        <p class="muted inspect-tagnote">Every extra tag on the same card pays less, down to 1 coin. A click on a tag removes it and takes back the coins it paid.</p>
      </div>
      ${c.reviewAvailable ? `<div data-tour="panel-review"><button class="btn" type="button" data-act="review">${c.reviewReward != null ? `Review · +${c.reviewReward}` : 'Review'}</button></div>` : ''}
      ${c.upgradeCost !== null ? `<div class="inspect-actions" data-tour="panel-upgrade"><button class="btn btn-ghost" type="button" data-act="upgrade">Try upgrade · ${c.upgradeCost} coins · ${chance}%</button></div>` : ''}`
  }

  function leftHTML(c: Card): string {
    const next = bestAction(c, c.fuseCopies, treatmentName)
    return `${next ? `<p class="inspect-next" data-tour="panel-next">➜ ${esc(next)}</p>` : ''}${versionsHTML(c)}${tagsHTML(c)}`
  }

  /** The version the price column is about: the one picked, else the best one held, else Normal. */
  const shownTreatment = (c: Card) => priceT ?? (c.copies > 0 ? c.treatment : 'normal')

  function rightHTML(c: Card): string {
    const s = getState()
    const t = shownTreatment(c)
    const k = history?.treatment_value[t] ?? 1
    // The change named here is the one the chart below shows: the same range, from the same numbers.
    const change = rangeChange(c)
    const trend = rangeChangeText(change, range)
    const price = c.prices[t] ?? Math.round((history?.value ?? c.value) * k)
    const days = history?.days ?? []
    return `<h3>Price</h3>
      <div class="seg price-seg" role="group" aria-label="Which version's price">
        ${[...s.treatments].sort((a, b) => a.tier - b.tier).map((x) =>
          `<button type="button" data-price-t="${esc(x.id)}" aria-pressed="${x.id === t}" style="--rarity:${visualFor(x.id).color}">${esc(x.name)}</button>`).join('')}
      </div>
      <p class="price-today">A ${esc(treatmentName(t))} copy is worth <b class="price-now">${coin(price)}</b> today${trend ? ` <span class="${change > 0 ? 'is-up' : 'is-down'}" data-el="price-change">${trend}</span>` : ` <span class="muted" data-el="price-change">· ${rangeChangeText(0, range, true)}</span>`}</p>
      ${history
        ? priceChartPanelHTML(history, range, t, { width: 460, height: 210 }) // a narrower drawing: its labels stay readable in the column
        : `<p class="muted">${historyFailed ? 'The price history could not be loaded.' : 'Loading the price history…'}</p>`}
      <h3 class="price-table-title">The last 30 days</h3>
      ${days.length ? `<div class="price-table-wrap"><table class="price-table">
        <thead><tr><th scope="col">Date</th><th scope="col">Price</th><th scope="col">Change</th></tr></thead>
        <tbody>${dayRows(days, k).map((r) => `<tr><td>${esc(r.date)}</td><td>${coin(r.price)}</td><td class="is-${r.dir}">${r.change}</td></tr>`).join('')}</tbody>
      </table></div>` : `<p class="muted">${history ? 'No daily prices yet. They come with the price updates.' : ''}</p>`}`
  }

  function render(focusTag = false) {
    if (!card) return
    const key = `${card.id}:${card.treatment}`
    if (key !== stageKey) {
      // A video card plays (muted) inside its own frame: there is no separate player next to the card.
      part('.inspect-stage').innerHTML = cardHTML(card, { size: 'lg' })
      stageKey = key
    } else {
      // refresh only the text and the ring of the card face, so a playing video (or GIF) keeps playing
      const face = d.querySelector<HTMLElement>('.inspect-stage .card')
      if (face) {
        const fresh = document.createElement('div')
        fresh.innerHTML = cardHTML(card, { size: 'lg' })
        for (const sel of ['.card-head', '.card-foot', '.vring']) {
          const now = face.querySelector(sel)
          const next = fresh.querySelector(sel)
          if (now && next) now.replaceWith(next)
        }
      }
    }
    const typed = (d.querySelector<HTMLInputElement>('[data-input="tag"]')?.value) ?? ''
    const parent = (d.querySelector<HTMLInputElement>('[data-input="parent"]')?.value) ?? ''
    part('.inspect-head').innerHTML = headHTML(card)
    part('.inspect-left').innerHTML = leftHTML(card)
    part('.inspect-right').innerHTML = rightHTML(card)
    const t = d.querySelector<HTMLInputElement>('[data-input="tag"]')
    const p = d.querySelector<HTMLInputElement>('[data-input="parent"]')
    if (t) t.value = typed
    if (p) p.value = parent
    if (focusTag) t?.focus()
    if (renaming) { const r = d.querySelector<HTMLInputElement>('[data-rename]'); r?.focus(); r?.select() }
    // Related tags under the tag field. The field is new after every render, so the list is attached again.
    suggest?.destroy()
    suggest = t ? attachTagSuggest(t, { mediaId: () => id, onPick: (name) => { t.value = name; void add(false) } }) : null
  }

  async function loadHistory() {
    try {
      history = await fetchCardHistory(id)
      historyFailed = false
    } catch { historyFailed = true }
    if (!busy && card && d.isConnected) part('.inspect-right').innerHTML = rightHTML(card)
  }

  /** Refresh the card and balance after a server action that already succeeded. Never throws. */
  async function sync(): Promise<boolean> {
    let ok = true
    try {
      const fresh = await fetchCard(id)
      card = fresh
      // A card without a copy is not in the collection (the last one was sold): the panel goes on showing it.
      if (fresh.copies > 0) replaceCard(fresh); else removeCard(id)
    } catch { ok = false }
    void loadHistory()
    await refreshWallet().catch(() => {})
    return ok
  }
  const STALE = 'Saved. Refresh to see the latest.'

  const coinsOrigin = () => d.querySelector('.inspect-left') ?? d

  async function run(fn: () => Promise<void>, focusTag = false) {
    if (busy) return
    busy = true
    try { await fn() } catch (e) {
      message = errorMessage(e)
      // The server refused (e.g. "Fusing needs 4 copies, you have 3"): what the panel shows is out
      // of date. Fetch the card again, so the button that can't work is gone after the redraw.
      if (e instanceof ApiError && e.status === 409) await sync()
    } finally {
      busy = false
      if (d.isConnected) render(focusTag)
    }
  }

  /**
   * A sale or a merge went through. The context is kept: the panel stays, with the ring and the
   * pill updated. Only a view that asked for it (the Missing-versions list entered from the pill
   * or from Home) gets the player back: the panel closes and the view is told.
   */
  function acted(text: string) {
    if (!opts.afterAction) { message = text; return }
    d.close()
    toast(text, { kind: 'coin' })
    opts.afterAction(text)
  }

  /** `typed`: the tag was typed and sent with Enter (true), or picked from the list of related tags (false). */
  async function add(typed = true) {
    const input = d.querySelector<HTMLInputElement>('[data-input="tag"]')!
    const parentInput = d.querySelector<HTMLInputElement>('[data-input="parent"]')!
    const name = normalizeTagInput(input.value)
    if (!name) { message = 'Tags need 1 to 40 characters'; return render(true) }
    const parentName = normalizeTagInput(parentInput.value) ?? undefined
    await run(async () => {
      message = ''
      const r = await addTag(id, name, parentName, typed)
      card = r.card
      replaceCard(r.card)
      const gained = gainedCoins(r) // tag reward plus milestone bonuses, as in Organize
      if (gained > 0) { floatCoins(gained, coinsOrigin(), d); play('coin') }
      // The tagging pause: say what to do next, and that a tag sent in the pause paid nothing.
      message = pauseText(pauseFrom(r), Date.now(), Boolean(r.paused))
      input.value = ''
      await refreshWallet().catch(() => {})
      void loadSuggestions()
    }, true)
  }

  async function loadSuggestions() {
    try {
      suggestions = (await fetchTags()).map((t) => t.name)
      const dl = d.querySelector('#inspect-tag-list')
      if (dl) dl.innerHTML = suggestions.map((s) => `<option value="${esc(s)}"></option>`).join('')
    } catch { /* suggestions are optional */ }
  }

  /** Sell copies. Asks first only when the sale empties a version slot or the card leaves the collection. */
  async function sell(tr: string, count: number) {
    if (!card || busy) return
    const question = saleDialog(card, tr, count)
    if (question && !(await confirmDialog(question))) return
    const leaves = card.copies <= count
    await run(async () => {
      message = ''
      const r = await sellCard(id, tr, count)
      play('coin')
      floatCoins(r.price, coinsOrigin(), d)
      applySlots(r.slots)
      const lost = r.slots && r.slots.delta < 0 ? ` · ${netVersionsText(r.slots.delta)}` : ''
      const text = `Sold ${r.sold} ${treatmentName(tr)} ${r.sold === 1 ? 'copy' : 'copies'} for ${r.price.toLocaleString()} coins${lost}${leaves ? '. The card left your collection.' : ''}`
      const ok = await sync()
      acted(ok ? text : `${text}. ${STALE}`)
    })
  }

  /** Merge copies into the next treatment. Asks first only when the merge empties a version slot. */
  async function fuse(tr: string) {
    if (!card || busy) return
    let option = card.fuseOptions.find((o) => o.from === tr)
    if (option?.empties) {
      // The count moves with the price: read the card fresh before the question names it.
      try {
        const fresh = await fetchCard(id)
        card = fresh
        if (fresh.copies > 0) replaceCard(fresh)
        option = fresh.fuseOptions.find((o) => o.from === tr)
      } catch { /* ask with what is on screen */ }
      if (!option?.can) { message = option ? `Merging needs ${option.copies} copies, you have ${option.have}.` : 'These copies cannot be merged.'; return render() }
      if (option.empties && !(await confirmDialog(fuseDialog(card, option)))) return
    }
    await run(async () => {
      message = ''
      const r = await fuseCard(id, tr)
      toastMilestones(r.milestones ?? [])
      applySlots(r.slots)
      flash('white', 0.6, d)
      play('reveal-1')
      const net = r.net_versions ?? r.slots?.delta ?? 0
      const text = `Merged ${r.used} ${treatmentName(tr)} copies into one ${treatmentName(r.treatment)} · ${netVersionsText(net)}`
      const ok = await sync()
      acted(ok ? text : `${text}. ${STALE}`)
    })
  }

  d.addEventListener('click', (e) => {
    const t = e.target as HTMLElement
    if (t === d || t.closest('[data-act="close"]')) return d.close()
    // The card itself: show the card full screen.
    if (card && t.closest('.inspect-stage .card')) return void openLightbox(card)
    // A miniature: that version full screen, also one that is not owned (a preview).
    const mini = t.closest<HTMLElement>('[data-version]')
    if (mini && card) {
      const v = mini.dataset.version!
      const held = card.copies > 0 && (card.copiesByTreatment[v] ?? 0) > 0
      return void openLightbox({ ...card, treatment: v }, held ? undefined : `Not owned · a preview of the ${treatmentName(v)} version`)
    }
    const rm = t.closest<HTMLElement>('[data-remove]')
    if (rm) {
      const tagId = Number(rm.dataset.remove)
      const name = card ? card.tags[card.tagIds.indexOf(tagId)] ?? '' : ''
      return void run(async () => {
        message = ''
        const r = await removeTag(id, tagId)
        // v0.9.0: the coins that tag had paid are taken back, and the panel says how many
        if (r && r.refunded > 0) { floatCoins(-r.refunded, coinsOrigin(), d); play('click') }
        const text = r ? removedTagText(name, r.refunded) : ''
        await refreshWallet().catch(() => {})
        message = (await sync()) ? text : `${text} ${STALE}`.trim()
      }, true)
    }
    const act = t.closest<HTMLElement>('[data-act]')?.dataset.act
    if (act === 'rename') { renaming = true; message = ''; return render() }
    if (act === 'rename-cancel') { renaming = false; return render() }
    if (act === 'rename-save') return void rename()
    if (act === 'review') {
      return void run(async () => {
        message = ''
        const r = await reviewCard(id)
        card = r.card
        replaceCard(r.card)
        if (r.coins_awarded > 0) { floatCoins(r.coins_awarded, coinsOrigin(), d); play('coin') }
        await refreshWallet().catch(() => {})
      })
    }
    if (act === 'upgrade') {
      // Upgrade attempts are switched off in the live config: the button only exists while the server offers a cost.
      return void run(async () => {
        message = ''
        const r = await upgradeCard(id)
        toastMilestones(r.milestones)
        applySlots(r.slots)
        if (r.success) {
          message = `Upgraded to ${treatmentName(r.treatment)}!`
          flash('white', 0.6, d)
          play('reveal-1')
        } else {
          message = 'Missed. The coins are gone, the card stays. The chase goes on.'
          play('near-miss')
        }
        if (!(await sync())) message += ` ${STALE}`
      })
    }
    const rangeBtn = t.closest<HTMLElement>('[data-range]')
    if (rangeBtn && card) {
      range = rangeBtn.dataset.range as Range
      part('.inspect-right').innerHTML = rightHTML(card)
      if (!renaming) part('.inspect-head').innerHTML = headHTML(card) // the header names the same range
      part(`.pc-range[data-range="${range}"]`)?.focus({ preventScroll: true })
      return
    }
    const priceBtn = t.closest<HTMLElement>('[data-price-t]')
    if (priceBtn && card) { priceT = priceBtn.dataset.priceT!; part('.inspect-right').innerHTML = rightHTML(card); return }
    const sellBtn = t.closest<HTMLElement>('[data-sell]')
    if (sellBtn) return void sell(sellBtn.dataset.sell!, Math.max(1, Number(sellBtn.dataset.count) || 1))
    const fuseBtn = t.closest<HTMLElement>('[data-fuse]')
    if (fuseBtn) return void fuse(fuseBtn.dataset.fuse!)
  })
  async function rename() {
    const field = d.querySelector<HTMLInputElement>('[data-rename]')
    if (!field) return
    const typed = field.value
    await run(async () => {
      message = ''
      const fresh = await renameCard(id, typed)
      card = fresh
      replaceCard(fresh)
      stageKey = '' // the name is on the card face too
      renaming = false
    })
  }

  d.addEventListener('keydown', (e) => {
    const t = e.target as HTMLElement
    if (t.matches('[data-rename]')) {
      if (e.key === 'Enter') { e.preventDefault(); void rename() }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); renaming = false; render() } // close the field, not the panel
      return
    }
    if (e.key === 'Enter' && t.matches('[data-input]')) {
      e.preventDefault()
      return void add()
    }
    // A focused card (the big one, or a miniature) acts like a button.
    if ((e.key === 'Enter' || e.key === ' ') && t.matches('.card')) { e.preventDefault(); t.click() }
  })
  // The store reloads every card when the prices change (data/store.ts). The panel shows the fresh
  // one: value, sell prices, wanted mark and merge count are those of the new price update.
  const off = subscribe((s) => {
    if (busy || !card || !d.open) return
    const fresh = s.cards.find((c) => c.id === id)
    if (!fresh || fresh === card) return
    card = fresh
    if (renaming) return // a name is being typed: the next redraw shows the fresh card
    if (history) void loadHistory()
    render(d.querySelector('[data-input="tag"]') === document.activeElement) // typing a tag goes on
  })
  // A link inside the panel ("Find in packs") goes to another page: the panel does not stay on top of it.
  const onHash = () => { if (d.open) d.close() }
  window.addEventListener('hashchange', onHash)
  d.addEventListener('close', () => { off(); window.removeEventListener('hashchange', onHash); suggest?.destroy(); d.remove(); onClose?.() })
  enableTilt(d)
  document.body.append(d)
  d.showModal()

  const start = () => {
    render()
    void loadHistory()
    if (card && card.copies > 0) { void loadSuggestions(); void firstVisitTour('card-panel') }
    if (opts.focus === 'price') {
      const right = part('.inspect-right')
      right.classList.add('is-focus')
      right.scrollIntoView({ block: 'nearest' })
      part('.price-seg button[aria-pressed="true"]')?.focus({ preventScroll: true })
    }
  }
  if (card) return start()
  // Not in the collection (a card named in the news, or one for sale): fetch it and show it read-only.
  part('.inspect-head').innerHTML = '<p class="muted">Loading the card…</p><button class="icon-btn inspect-x" type="button" data-act="close" aria-label="Close">✕</button>'
  fetchCard(id).then((c) => {
    if (!d.isConnected) return
    card = c
    start()
  }).catch(() => {
    if (d.open) d.close()
    toast('This card could not be loaded.', { kind: 'warn' })
  })
}
