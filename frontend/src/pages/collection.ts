/**
 * Collection: the cards you PULLED, in a virtualized grid or a binder, with filters and
 * click-to-inspect. A photo or video that never came out of a pack is not shown anywhere: it is
 * still in a pack. The counter says how many of all cards you have pulled.
 */

import { cardHTML, copiesLabel, enableTilt, esc } from '../cards/card.ts'
import { visualFor } from '../cards/treatments.ts'
import { bulkTag, fetchCard, fetchTags, type ApiTagCount } from '../data/api.ts'
import { bulkRuleText, chunk } from '../data/bulk.ts'
import { completeness, getState, initData, missingVersions, refreshWallet, replaceCard, subscribe, VERSION_IDS } from '../data/store.ts'
import type { Card, TreatmentInfo } from '../data/types.ts'
import { floatCoins } from '../fx/effects.ts'
import { play } from '../fx/sound.ts'
import { errorMessage, normalizeTagInput, openInspect } from './inspect.ts'
import { buildTagTree, filterCounts, tagFilterIds, tagTreeHTML } from '../ui/tagBrowser.ts'
import { attachTagSuggest } from '../ui/tagInput.ts'
import { createVirtualGrid, type VirtualGrid } from '../ui/virtualGrid.ts'
import type { Page } from '../app/router.ts'
import { overlayOpen } from '../ui/overlay.ts'
import { openLightbox } from '../ui/lightbox.ts'

type View = 'grid' | 'binder'
/** v0.9.0 (D7): nine pockets a page, 3 × 3, two pages a spread. */
export const BINDER_COLS = 3
export const BINDER_PER_PAGE = BINDER_COLS * BINDER_COLS

/**
 * How wide a card in a binder pocket is, in px: it follows the size slider (0.8 of the grid's card
 * width, so the default slider gives the binder's usual 120 px) and never grows past what lets
 * both pages fit side by side in `available` px (one page when the pages stack on a narrow window).
 */
export function binderCardWidth(cardWidth: number, available: number, stacked = false): number {
  const want = Math.round(clampCardWidth(cardWidth) * 0.8)
  const pages = stacked ? 1 : 2
  // the binder's own padding and border (38), the rings (50, not when stacked), and per page its padding (12) and two gaps (24)
  const chrome = 38 + (stacked ? 0 : 50) + pages * 36
  const fit = Math.floor((available - chrome) / (pages * BINDER_COLS)) - 12 // 12: the pocket is that much wider than its card
  return Math.max(56, Math.min(want, Number.isFinite(fit) && available > 0 ? fit : want))
}

let view: View = (() => {
  try { return (localStorage.getItem('mco.collection.view') as View) || 'grid' } catch { return 'grid' }
})()

export type Sort = 'recent' | 'value' | 'value-asc' | 'rarity' | 'copies' | 'name' | 'missing' | 'mergeable'
export const SORTS: [Sort, string][] = [
  ['recent', 'Order pulled'], ['value', 'Value: high to low'], ['value-asc', 'Value: low to high'],
  ['rarity', 'Rarest first'], ['copies', 'Most copies'], ['name', 'Name'], ['missing', 'Fewest missing versions'], ['mergeable', 'Mergeable first'],
]

/** The card has enough copies of some treatment to merge them right now. */
export const canMerge = (card: Pick<Card, 'fuseOptions'>): boolean => card.fuseOptions.some((o) => o.can)
/** The filter that lists pulled cards lacking a Foil, Holo or Special. It brings its own sort. */
export const MISSING_FILTER = 'missing'
export const CARD_MIN = 110
export const CARD_MAX = 340
export const CARD_DEFAULT = 150

function stored(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}
function store(key: string, value: string): void {
  try { localStorage.setItem(key, value) } catch { /* private mode */ }
}

/** A stored or typed card width, brought into the slider's range. */
export function clampCardWidth(v: unknown): number {
  const n = Math.round(Number(v))
  return Number.isFinite(n) && n > 0 ? Math.min(CARD_MAX, Math.max(CARD_MIN, n)) : CARD_DEFAULT
}

/** The grid cell for a card this wide: the card (63:88) plus two lines: its value, and the copies held. */
export function cellSize(cardWidth: number): { width: number; height: number } {
  return { width: cardWidth, height: Math.round(cardWidth * 88 / 63) + 56 }
}

/** A price move worth a badge on the thumbnail: more than this share over the last 24 hours (the card's `trend`). */
export const PRICE_MOVE_MIN = 0.03

/**
 * The price-move badge of a card, or null. It shows when the player holds exactly one of Foil,
 * Holo and Special of the card (one version to decide about) and the price moved more than 3%.
 */
export function priceMove(card: Pick<Card, 'copies' | 'copiesByTreatment' | 'trend'>): { dir: 'up' | 'down'; text: string } | null {
  if (card.copies < 1 || Math.abs(card.trend) <= PRICE_MOVE_MIN) return null
  if (VERSION_IDS.filter((t) => (card.copiesByTreatment[t] ?? 0) > 0).length !== 1) return null
  const up = card.trend > 0
  return { dir: up ? 'up' : 'down', text: `${up ? '▲' : '▼'} ${Math.round(Math.abs(card.trend) * 100)}%` }
}

const DISMISSED_KEY = 'mco.pricemove.dismissed'

/** The cards whose price-move badge was dismissed, for this price update only (an older update's list is dropped). */
export function dismissedMoves(tick: number, raw: string | null): Set<number> {
  try {
    const saved = JSON.parse(raw ?? 'null') as { tick?: number; ids?: number[] } | null
    return new Set(saved && saved.tick === tick && Array.isArray(saved.ids) ? saved.ids : [])
  } catch { return new Set() }
}

/** A sorted copy. 'recent' keeps the order the cards came in. Ties keep that order too. */
export function sortCards(cards: Card[], sort: Sort, treatments: TreatmentInfo[] = []): Card[] {
  if (sort === 'recent') return cards
  const tier = new Map(treatments.map((t) => [t.id as string, t.tier]))
  const by: Record<Exclude<Sort, 'recent'>, (a: Card, b: Card) => number> = {
    // The Missing-versions order: the cards closest to holding all three first, then the most valuable.
    missing: (a, b) => missingVersions(a).length - missingVersions(b).length || b.value - a.value,
    // v0.9.0: the cards whose copies can be merged right now first, then the most valuable.
    mergeable: (a, b) => Number(canMerge(b)) - Number(canMerge(a)) || b.value - a.value,
    value: (a, b) => b.value - a.value,
    'value-asc': (a, b) => a.value - b.value,
    rarity: (a, b) => (tier.get(b.treatment) ?? 0) - (tier.get(a.treatment) ?? 0) || b.value - a.value,
    copies: (a, b) => b.copies - a.copies,
    name: (a, b) => a.title.localeCompare(b.title),
  }
  return [...cards].sort(by[sort])
}

let sort: Sort = SORTS.some(([id]) => id === stored('mco.collection.sort')) ? stored('mco.collection.sort') as Sort : 'recent'
let binderVersions = stored('mco.collection.binderVersions') === '1'
let cardWidth = clampCardWidth(stored('mco.collection.cardWidth'))

export function filterCards(cards: Card[], filter: string): Card[] {
  if (filter === 'all') return cards
  if (filter === 'untagged') return cards.filter((c) => c.tags.length === 0)
  if (filter === MISSING_FILTER) return cards.filter((c) => c.copies > 0 && missingVersions(c).length > 0)
  if (filter === 'owned' || filter === 'pulled') return cards.filter((c) => c.copies > 0)
  // A treatment filter lists cards you own in that treatment; a card never pulled has none yet.
  return cards.filter((c) => c.copies > 0 && c.treatment === filter)
}

/** What an empty result says, with the action that fills it (never a blank page). */
export function emptyHTML(filter: string, tagged: boolean): string {
  const [text, href, label] = filter === 'untagged'
    ? ['Every card here is tagged. Rarer copies are the chase now.', '#/packs', 'Open packs']
    : filter === MISSING_FILTER
      ? ['No card you pulled has a missing version right now. New cards come out of packs.', '#/packs', 'Open packs']
    : filter === 'all' && !tagged
      ? ['Your binder is empty. Every card comes out of a pack: open one to pull your first cards.', '#/packs', 'Open packs']
      : filter === 'all'
        ? ['No cards carry this tag.', '#/organize', 'Tag cards']
        : ['Nothing here yet. Pull it from a pack.', '#/packs', 'Open packs']
  return `<div class="coll-empty"><p>${text}</p><a class="btn" href="${href}">${label}</a></div>`
}

/** Keep cards carrying any of the given tag ids (null = no tag filter). */
export function filterByTagIds(cards: Card[], ids: Set<number> | null): Card[] {
  return ids ? cards.filter((c) => c.tagIds.some((t) => ids.has(t))) : cards
}

export const collectionPage: Page = {
  id: 'collection',
  title: 'Collection',
  icon: '▦',
  mount(el) {
    const s = getState()
    const params = new URLSearchParams(location.hash.split('?')[1] ?? '')
    let filter = params.get('filter') ?? 'all'
    // How the Collection was entered: from the version pill or from Home's "Fill versions". After a
    // sale or a merge in the card panel the view then returns to the Missing-versions filter.
    const from = params.get('from') === 'pill' || params.get('from') === 'home' ? params.get('from') : null
    let ownSort = false // the player picked a sort while the Missing-versions filter was on
    const activeSort = (): Sort => (filter === MISSING_FILTER && !ownSort ? 'missing' : sort)
    const tick = () => getState().wallet.market.tick
    let dismissed = dismissedMoves(tick(), stored(DISMISSED_KEY))
    const tagParam = Number(params.get('tag'))
    let activeTag: number | null = Number.isInteger(tagParam) && tagParam > 0 && s.mode === 'live' ? tagParam : null
    let tagList: ApiTagCount[] = []
    let tagsLoaded = !(activeTag !== null) // with ?tag=, wait for the tag list before the first grid render
    let disposed = false
    let spread = 0
    let grid: VirtualGrid<Card> | null = null
    const c = completeness()

    el.innerHTML = `
      <div class="coll-toolbar">
        <div class="coll-progress" data-tour="completeness">
          <span class="coll-count"><b>${c.owned.toLocaleString()}</b> / ${c.total.toLocaleString()}</span>
          <span class="muted">cards pulled · <span data-el="tagged-n">${c.tagged.toLocaleString()}</span> tagged ·
            <a class="coll-worth" href="#/market" title="What all your copies are worth on the market today">worth 🪙 <b data-el="worth">${s.wallet.collectionValue.toLocaleString()}</b></a></span>
          <div class="progress"><div class="progress-fill" style="width:${c.pulled * 100}%"></div></div>
        </div>
        <div class="chips" role="group" aria-label="Filter">
          ${[['all', 'All'], ['untagged', 'Untagged'], [MISSING_FILTER, 'Missing versions'], ...s.treatments.map((t) => [t.id, t.name])]
            .map(([id, label]) => `<button class="chip" data-filter="${id}"${id === MISSING_FILTER ? ' data-tour="filter-missing" title="Cards you pulled that lack a Foil, Holo or Special"' : ''}>${label}</button>`).join('')}
        </div>
        <button class="chip" data-tags-toggle aria-pressed="false" aria-controls="tag-panel" title="Browse by tag or category">🏷 Tags</button>
        <span class="chip tag-active" data-tag-active hidden><span data-tag-active-name></span><button class="chip-x" data-tag-clear type="button" aria-label="Remove tag filter">×</button></span>
        <button class="chip" data-select-toggle aria-pressed="false" title="Select several cards to tag them together">☑ Select</button>
        <label class="coll-sort">Sort <select data-sort aria-label="Sort the cards">
          ${SORTS.map(([id, label]) => `<option value="${id}"${id === activeSort() ? ' selected' : ''}>${label}</option>`).join('')}
        </select></label>
        <label class="coll-size" title="Card size">▫<input type="range" data-size min="${CARD_MIN}" max="${CARD_MAX}" step="2" value="${cardWidth}" aria-label="Card size">◻</label>
        <div class="seg" role="group" aria-label="View">
          <button data-view="grid" title="Grid">▦ Grid</button>
          <button data-view="binder" title="Binder">📖 Binder</button>
        </div>
      </div>
      <p class="coll-missing" data-el="missing-line" hidden></p>
      <div class="coll-layout">
        <aside class="tag-panel" id="tag-panel" aria-label="Tags" hidden></aside>
        <div class="coll-body"></div>
      </div>
      <div class="bulk-bar" hidden>
        <span class="bulk-count"></span>
        <input class="input" data-bulk-input list="bulk-tag-list" maxlength="60" placeholder="Tag for all selected" aria-label="Tag for all selected" autocomplete="off">
        <datalist id="bulk-tag-list"></datalist>
        <button class="btn" data-bulk-apply type="button">Apply</button>
        <button class="btn btn-ghost" data-bulk-clear type="button">Clear</button>
        <span class="bulk-rule muted" data-el="bulk-rule" title="One tag on many cards at once pays less than tagging each card in Organize. Every paid tag still pays at least 1 coin."></span>
        <span class="bulk-msg muted" role="status" aria-live="polite"></span>
      </div>`

    const body = el.querySelector<HTMLDivElement>('.coll-body')!
    enableTilt(body)

    const live = s.mode === 'live'
    let selectMode = false
    const selected = new Set<number>()
    let bulkBusy = false

    const box = (id: number) => selectMode
      ? `<span class="sel-box" aria-hidden="true">${selected.has(id) ? '✓' : ''}</span>` : ''
    const selCls = (id: number) => (selectMode && selected.has(id) ? ' is-selected' : '')

    function updateBar(msg?: string) {
      const bar = el.querySelector<HTMLElement>('.bulk-bar')!
      const toggle = el.querySelector<HTMLElement>('[data-select-toggle]')!
      toggle.setAttribute('aria-pressed', String(selectMode))
      bar.hidden = !selectMode
      const n = selected.size
      el.querySelector('.bulk-count')!.textContent = live ? `${n} selected` : ''
      const hasSel = live && n > 0
      el.querySelector<HTMLElement>('[data-bulk-input]')!.hidden = !live
      el.querySelector<HTMLElement>('[data-bulk-apply]')!.hidden = !live
      el.querySelector<HTMLButtonElement>('[data-bulk-apply]')!.disabled = !hasSel || bulkBusy
      el.querySelector<HTMLElement>('[data-bulk-clear]')!.hidden = !live
      el.querySelector<HTMLElement>('[data-el="bulk-rule"]')!.textContent = live ? bulkRuleText(getState().tagging.bulkMultiplier) : ''
      if (msg !== undefined) el.querySelector('.bulk-msg')!.textContent = msg
      if (!live) el.querySelector('.bulk-msg')!.textContent = 'Available once your media is imported'
    }

    function updateProgress() {
      const c2 = completeness()
      el.querySelector('.coll-count')!.innerHTML = `<b>${c2.owned.toLocaleString()}</b> / ${c2.total.toLocaleString()}`
      el.querySelector('[data-el="tagged-n"]')!.textContent = c2.tagged.toLocaleString()
      el.querySelector('[data-el="worth"]')!.textContent = getState().wallet.collectionValue.toLocaleString()
      el.querySelector<HTMLElement>('.progress-fill')!.style.width = `${c2.pulled * 100}%`
    }

    const items = () => sortCards(
      filterByTagIds(filterCards(getState().cards, filter), activeTag === null ? null : tagFilterIds(tagList, activeTag)),
      activeSort(), getState().treatments)
    // Under the card: what it is worth and which way it moved; below that the copies held, by treatment.
    const worth = (card: Card) => `<span class="vcell-value${card.wanted ? ' is-wanted' : ''}" title="${card.wanted ? 'A collector wants this card: it sells for more right now' : 'Market value of your best copy'}">🪙 ${card.value.toLocaleString()}${card.trend > 0.005 ? ' <i class="is-up">▲</i>' : card.trend < -0.005 ? ' <i class="is-down">▼</i>' : ''}${card.wanted ? ' ★' : ''}</span>${card.copies > 1 ? `<span class="vcell-copies" title="Copies you hold: ${esc(copiesLabel(card.copiesByTreatment))}">${esc(copiesLabel(card.copiesByTreatment, cardWidth < 190))}</span>` : ''}`
    // The price-move badge: one version held and the price moved more than 3%. It opens the panel at the price.
    const moveBadge = (card: Card) => {
      const m = dismissed.has(card.id) ? null : priceMove(card)
      return m ? `<span class="pm-badge is-${m.dir}"><button type="button" class="pm-open" data-pm-open="${card.id}" title="The price moved ${m.text} in the last 24 hours. Open the price.">${m.text}</button><button type="button" class="pm-x" data-pm-x="${card.id}" aria-label="Hide this price move until the next update" title="Hide until the next update">×</button></span>` : ''
    }

    const panel = el.querySelector<HTMLElement>('#tag-panel')!
    const tagsBtn = el.querySelector<HTMLButtonElement>('[data-tags-toggle]')!
    if (!live) { tagsBtn.disabled = true; tagsBtn.title = 'Tag browsing is available once your media is imported' }

    function syncUrl() {
      const q = new URLSearchParams()
      if (filter !== 'all') q.set('filter', filter)
      if (from) q.set('from', from)
      if (activeTag !== null) q.set('tag', String(activeTag))
      const qs = q.toString()
      history.replaceState(null, '', `#/collection${qs ? `?${qs}` : ''}`)
    }

    function renderTags() {
      const chip = el.querySelector<HTMLElement>('[data-tag-active]')!
      chip.hidden = activeTag === null
      if (activeTag !== null) {
        const name = tagList.find((t) => t.id === activeTag)?.name
        el.querySelector('[data-tag-active-name]')!.textContent = name ? `Tag: ${name}` : tagsLoaded ? 'Tag not found' : 'Tag…'
      }
      const focusedTag = (document.activeElement as HTMLElement | null)?.closest?.('[data-tag]')?.getAttribute('data-tag')
      // Show the number a click will actually produce (distinct cards, children included).
      const counts = filterCounts(tagList, s.cards)
      panel.innerHTML = tagTreeHTML(buildTagTree(tagList.map((t) => ({ ...t, count: counts.get(t.id) ?? 0 }))), activeTag)
      if (focusedTag) panel.querySelector<HTMLElement>(`[data-tag="${focusedTag}"]`)?.focus()
    }

    async function loadTags() {
      if (!live) return
      let failed = false
      try { tagList = await fetchTags() } catch { failed = true }
      if (disposed) return
      const first = !tagsLoaded
      tagsLoaded = true
      if (failed && !first) return
      renderTags()
      if (activeTag !== null) render() // child ids are known now
    }

    function setTag(id: number | null) {
      activeTag = id
      spread = 0
      syncUrl()
      renderTags()
      render()
      if (id !== null) togglePanel(false, true)
    }

    // Wide screens: panel is a side column; under 760px it is a drawer. Either way the button toggles it.
    let userOpened = false
    function togglePanel(open?: boolean, onlyNarrow = false) {
      if (onlyNarrow && !window.matchMedia('(max-width: 760px)').matches) return
      const next = open ?? panel.hidden
      const wasOpen = !panel.hidden
      panel.hidden = !next
      tagsBtn.setAttribute('aria-pressed', String(next))
      if (next && !wasOpen && onlyNarrow === false && userOpened) panel.querySelector<HTMLElement>('[data-tag]')?.focus()
      if (!next && wasOpen && panel.contains(document.activeElement)) tagsBtn.focus()
    }

    function render() {
      el.querySelectorAll<HTMLButtonElement>('[data-filter]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.filter === filter)))
      el.querySelectorAll<HTMLButtonElement>('[data-view]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.view === view)))
      el.querySelector<HTMLSelectElement>('[data-sort]')!.value = activeSort()
      // The Missing-versions view always says where versions come from, also when the list is empty.
      const line = el.querySelector<HTMLElement>('[data-el="missing-line"]')!
      line.hidden = filter !== MISSING_FILTER
      if (filter === MISSING_FILTER) {
        const n = items().length
        line.innerHTML = `<span>${n.toLocaleString()} ${n === 1 ? 'card has' : 'cards have'} a missing version. Fewest missing first.</span>
          <a class="coll-missing-link" href="#/packs">Open packs to fill versions</a>`
      }
      grid?.destroy()
      grid = null
      body.innerHTML = ''
      body.className = `coll-body coll-${view}${view === 'binder' && binderVersions ? ' coll-binder-versions' : ''}`
      if (items().length === 0) {
        body.innerHTML = emptyHTML(filter, activeTag !== null)
        return
      }
      if (view === 'grid') {
        const cell = cellSize(cardWidth)
        body.style.setProperty('--cell-w', `${cell.width}px`)
        body.style.setProperty('--cell-h', `${cell.height}px`)
        grid = createVirtualGrid(body, {
          items: items(), cellWidth: cell.width, cellHeight: cell.height, gap: 18,
          render: (card) => `<div class="vcell${selCls(card.id)}">${box(card.id)}${cardHTML(card)}${moveBadge(card)}${worth(card)}</div>`,
        })
      } else renderBinder()
    }

    /** Redraw after cards changed (a tag, a selection) without losing the place in the grid. */
    function refresh() {
      if (view === 'grid' && grid) grid.refresh(items())
      else render()
    }

    /** How many spreads the binder has: 18 cards a spread, or one card a spread in the Versions layout. */
    const spreadCount = (n: number) => Math.max(1, binderVersions ? n : Math.ceil(n / (BINDER_PER_PAGE * 2)))

    function renderBinder() {
      const list = items()
      const spreads = spreadCount(list.length)
      spread = Math.min(spread, spreads - 1)
      const pageCards = (p: number) => list.slice(p * BINDER_PER_PAGE, (p + 1) * BINDER_PER_PAGE)
      // The pockets follow the size slider, as far as both pages fit the width.
      const stacked = window.matchMedia('(max-width: 760px)').matches
      body.style.setProperty('--bw', `${binderCardWidth(cardWidth, body.clientWidth - 8, stacked)}px`)
      const pocket = (card?: Card) => `<div class="pocket${card ? selCls(card.id) : ''}">${card ? box(card.id) + cardHTML(card, { size: 'sm', showCount: true }) + moveBadge(card) : ''}</div>`
      const pageHTML = (p: number) => {
        const cards = pageCards(p)
        return `<div class="binder-page">${Array.from({ length: BINDER_PER_PAGE }, (_, i) => pocket(cards[i])).join('')}</div>`
      }
      // The Versions layout: one card a spread, in four pockets (Normal, Foil | Holo, Special), each
      // with the treatment's look. A version that is not held is a visible hole.
      const one = list[spread]
      const tiers = [...getState().treatments].sort((a, b) => a.tier - b.tier)
      const versionPocket = (t: TreatmentInfo) => {
        const n = one.copiesByTreatment[t.id] ?? 0
        const color = visualFor(t.id).color
        return n > 0
          ? `<div class="pocket pocket-version" style="--rarity:${color}">${cardHTML({ ...one, treatment: t.id }, { size: 'md', ring: false })}
               <span class="pocket-label"><b>${esc(t.name)}</b> ×${n}</span></div>`
          : `<div class="pocket pocket-version is-hole" style="--rarity:${color}">
               <button type="button" class="pocket-hole" data-hole="${one.id}" data-hole-t="${esc(t.id)}" title="${esc(one.title)}: you hold no ${esc(t.name)} copy. See it full screen (a preview).">
                 <b>${esc(t.name)}</b><span>Not owned</span><span class="pocket-hole-more">Preview</span></button>
               <span class="pocket-label muted">${esc(t.name)}</span></div>`
      }
      const versionPage = (from: number) => `<div class="binder-page binder-page-versions">${tiers.slice(from, from + 2).map(versionPocket).join('')}</div>`
      const held = one ? VERSION_IDS.filter((t) => (one.copiesByTreatment[t] ?? 0) > 0).length : 0
      body.innerHTML = `
        ${binderVersions ? `<p class="binder-title"><b title="${esc(one.title)}">${esc(one.title)}</b> <span class="muted">${esc(one.code)} · ${held} of ${VERSION_IDS.length} versions held</span></p>` : ''}
        <div class="binder${binderVersions ? ' binder-versions' : ''}">
          ${binderVersions ? versionPage(0) : pageHTML(spread * 2)}
          <div class="binder-rings" aria-hidden="true"><span></span><span></span><span></span></div>
          ${binderVersions ? versionPage(2) : pageHTML(spread * 2 + 1)}
        </div>
        <div class="binder-nav">
          <button class="btn btn-ghost" data-page="-10" title="Q">« 10</button>
          <button class="btn btn-ghost" data-page="-1" title="A">‹ Prev</button>
          <span class="binder-pageno">${binderVersions ? 'Card' : 'Page'} <b>${spread + 1}</b> / ${spreads}</span>
          <button class="btn btn-ghost" data-page="1" title="D">Next ›</button>
          <button class="btn btn-ghost" data-page="10" title="E">10 »</button>
          <button class="chip binder-toggle" type="button" data-binder-versions aria-pressed="${binderVersions}" title="One card a spread: its Normal, Foil, Holo and Special in four pockets">Versions</button>
        </div>
        <p class="muted binder-keys">Keys: A / D turn a ${binderVersions ? 'card' : 'page'} · Q / E jump 10</p>`
      body.querySelectorAll<HTMLButtonElement>('[data-page]').forEach((b) =>
        b.addEventListener('click', () => turn(Number(b.dataset.page))))
    }

    function turn(delta: number) {
      const spreads = spreadCount(items().length)
      const next = Math.min(spreads - 1, Math.max(0, spread + delta))
      if (next === spread) return
      spread = next
      play('flip')
      renderBinder()
    }

    const onKey = (e: KeyboardEvent) => {
      if (overlayOpen()) return
      if (e.key === 'Escape' && !panel.hidden && window.matchMedia('(max-width: 760px)').matches && !(e.target as HTMLElement).closest('dialog')) {
        togglePanel(false); return
      }
      if (view !== 'binder' || (e.target as HTMLElement).closest('input, textarea, dialog')) return
      const map: Record<string, number> = { a: -1, d: 1, q: -10, e: 10 }
      const d = map[e.key.toLowerCase()]
      if (d) { e.preventDefault(); turn(d) }
    }
    window.addEventListener('keydown', onKey)

    el.addEventListener('click', (e) => {
      const t = e.target as HTMLElement
      const f = t.closest<HTMLElement>('[data-filter]')
      if (f) { filter = f.dataset.filter!; ownSort = false; spread = 0; syncUrl(); return render() }
      if (t.closest('[data-binder-versions]')) {
        // Keep the place: the spread that held this card, or the card that opened this spread.
        spread = binderVersions ? Math.floor(spread / (BINDER_PER_PAGE * 2)) : spread * BINDER_PER_PAGE * 2
        binderVersions = !binderVersions
        store('mco.collection.binderVersions', binderVersions ? '1' : '0')
        return render()
      }
      const pmX = t.closest<HTMLElement>('[data-pm-x]')
      if (pmX) {
        dismissed.add(Number(pmX.dataset.pmX))
        store(DISMISSED_KEY, JSON.stringify({ tick: tick(), ids: [...dismissed] }))
        return refresh()
      }
      const pmOpen = t.closest<HTMLElement>('[data-pm-open]')
      if (pmOpen) return inspect(Number(pmOpen.dataset.pmOpen), 'price')
      const hole = t.closest<HTMLElement>('[data-hole]')
      if (hole) {
        // v0.9.0 (test round 6 point 11): a version that is not held opens full screen too, as a marked preview.
        const card = getState().cards.find((x) => x.id === Number(hole.dataset.hole))
        const tr = hole.dataset.holeT
        const name = getState().treatments.find((x) => x.id === tr)?.name ?? tr
        if (card && tr) return void openLightbox({ ...card, treatment: tr }, `Not owned · a preview of the ${name} version`)
        return inspect(Number(hole.dataset.hole))
      }
      if (t.closest('[data-tags-toggle]')) { userOpened = true; togglePanel(); userOpened = false; return }
      if (t.closest('[data-tag-clear]')) return setTag(null)
      const tg = t.closest<HTMLElement>('[data-tag]')
      if (tg) { const id = Number(tg.dataset.tag); return setTag(id === activeTag ? null : id) }
      const v = t.closest<HTMLElement>('[data-view]')
      if (v) {
        view = v.dataset.view as View
        store('mco.collection.view', view)
        return render()
      }
      if (t.closest('[data-select-toggle]')) {
        selectMode = !selectMode
        if (!selectMode) selected.clear()
        updateBar('')
        return refresh()
      }
      if (t.closest('[data-bulk-clear]')) { selected.clear(); updateBar(''); return refresh() }
      if (t.closest('[data-bulk-apply]')) return void applyBulk()
      const card = t.closest<HTMLElement>('.card')
      if (card && selectMode) {
        if (!live) return
        const id = Number(card.dataset.cardId)
        if (selected.has(id)) selected.delete(id); else selected.add(id)
        const cell = card.closest<HTMLElement>('.vcell, .pocket')
        cell?.classList.toggle('is-selected', selected.has(id))
        const mark = cell?.querySelector('.sel-box')
        if (mark) mark.textContent = selected.has(id) ? '✓' : ''
        return updateBar('')
      }
      if (card) inspect(Number(card.dataset.cardId))
    })

    /** Open the card panel. Entered from the pill or from Home: a sale or merge brings the Missing-versions list back. */
    function inspect(id: number, focus?: 'price') {
      openInspect(id, () => { if (disposed) return; refresh(); updateProgress(); void loadTags() }, {
        focus,
        afterAction: from ? () => {
          if (disposed) return
          filter = MISSING_FILTER
          ownSort = false
          spread = 0
          syncUrl()
          render()
        } : undefined,
      })
    }

    el.addEventListener('change', (e) => {
      const sel = (e.target as HTMLElement).closest<HTMLSelectElement>('[data-sort]')
      if (!sel) return
      sort = sel.value as Sort
      ownSort = true
      store('mco.collection.sort', sort)
      spread = 0
      render()
    })
    el.addEventListener('input', (e) => {
      const range = (e.target as HTMLElement).closest<HTMLInputElement>('[data-size]')
      if (!range) return
      cardWidth = clampCardWidth(range.value)
      store('mco.collection.cardWidth', String(cardWidth))
      render() // the grid's cells and the binder's pockets both follow the slider
    })

    async function loadBulkSuggestions() {
      if (!live) return
      try {
        const tags = await fetchTags()
        el.querySelector('#bulk-tag-list')!.innerHTML = tags.map((t) => `<option value="${esc(t.name)}"></option>`).join('')
      } catch { /* suggestions are optional */ }
    }

    el.addEventListener('keydown', (e) => {
      const t = e.target as HTMLElement
      if (e.key === 'Enter' && t.matches('[data-bulk-input]')) { e.preventDefault(); void applyBulk() }
      // A focused card acts like a button: Enter or Space does what a click does.
      if ((e.key === 'Enter' || e.key === ' ') && t.matches('.card')) { e.preventDefault(); t.click() }
    })

    async function applyBulk() {
      if (bulkBusy || selected.size === 0) return
      const input = el.querySelector<HTMLInputElement>('[data-bulk-input]')!
      const name = normalizeTagInput(input.value)
      if (!name) return updateBar('Tags need 1 to 40 characters')
      const ids = [...selected]
      const size = Math.max(1, getState().limits.bulk_max_items)
      bulkBusy = true
      updateBar('Applying…')
      let applied = 0
      let coins = 0
      let failure = ''
      const done: number[] = []
      try {
        for (const part of chunk(ids, size)) {
          const r = await bulkTag(part, name)
          applied += r.applied
          coins += r.coins_awarded
          done.push(...part)
        }
      } catch (e) {
        failure = errorMessage(e)
      }
      try {
        if (applied > 0) {
          if (ids.length <= 25) (await Promise.all(ids.map((id) => fetchCard(id)))).forEach(replaceCard)
          else await initData()
        }
      } catch { failure ||= 'Saved. Refresh to see the latest.' }
      await refreshWallet().catch(() => {})
      bulkBusy = false
      if (disposed) return
      if (coins > 0) { floatCoins(coins, el.querySelector('.bulk-bar')!); play('coin') }
      // Drop only the ids that were tagged: a retry must not resend them, and cards picked meanwhile stay.
      done.forEach((id) => selected.delete(id))
      if (!failure) input.value = ''
      void loadBulkSuggestions()
      void loadTags()
      refresh()
      updateProgress()
      updateBar(failure ? `Tagged ${applied}, then stopped: ${failure}` : `Tagged ${applied} cards${coins > 0 ? ` · +${coins} coins` : ''}`)
    }

    const bulkInput = el.querySelector<HTMLInputElement>('[data-bulk-input]')!
    const bulkSuggest = live ? attachTagSuggest(bulkInput, { onPick: (name) => { bulkInput.value = name; bulkInput.focus() } }) : null
    if (tagsLoaded) render()
    updateBar('')
    void loadBulkSuggestions()
    renderTags()
    void loadTags()
    if (live && !window.matchMedia('(max-width: 760px)').matches) togglePanel(true)
    // A new price update: the dismissed price-move badges belong to the old one.
    let seenTick = tick()
    const offStore = subscribe(() => {
      if (disposed || tick() === seenTick) return
      seenTick = tick()
      dismissed = dismissedMoves(seenTick, stored(DISMISSED_KEY))
      if (!document.querySelector('dialog[open]')) refresh()
    })
    return () => { disposed = true; offStore(); bulkSuggest?.destroy(); grid?.destroy(); window.removeEventListener('keydown', onKey) }
  },
}
