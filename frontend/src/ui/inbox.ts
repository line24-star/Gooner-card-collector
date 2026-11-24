/**
 * Message centre: a panel that slides out from the right with what needs the player's attention.
 * A collector's offer arrives here, and also slides in once as a small message when it is new.
 * Other messages: cards a collector wants, the shop deal, the tag rush, and the latest market news.
 *
 * Messages are not stored: they are read from the wallet, so they vanish by themselves when the
 * offer runs out or the deal ends. Only "which ones did I already see" is kept (in this browser).
 */

import './inbox.css'
import { esc } from '../cards/card.ts'
import { visualFor } from '../cards/treatments.ts'
import { answerOffer, fetchMarket, newsCards, type NewsItem } from '../data/marketApi.ts'
import { applySlots, getState, refreshWallet, reloadAll, subscribe, type AppState } from '../data/store.ts'
import { floatCoins } from '../fx/effects.ts'
import { play } from '../fx/sound.ts'
import { openInspect, wantedText } from '../pages/inspect.ts'
import { confirmDialog, offerDialog } from './confirm.ts'
import { countdownText, startCountdowns } from './countdown.ts'
import { newsNamesHTML } from './newsNames.ts'
import { overlayOpen } from './overlay.ts'
import { toast } from './toast.ts'

export interface Message {
  id: string
  kind: 'offer' | 'wanted' | 'deal' | 'rush'
  icon: string
  title: string
  body: string
  /** When it ends (ISO), for the clock. */
  until?: string | null
  href?: string
  /** A collector's offer: the numbers for the buttons. */
  offer?: {
    price: number; sellPrice: number; marketPrice: number; thumb: string | null; cardTitle: string
    /** The treatment the collector wants, and whether taking the offer empties its version slot. */
    treatment: string; emptiesSlot: boolean
    /** The card's only copy of all: taking the offer takes the card out of the collection. */
    lastCopy: boolean
  }
}

/** How a collector's price compares with what the market pays you right now. Never calls a lower price a gain. */
export function offerText(price: number, sellPrice: number, marketPrice: number): string {
  const diff = price - sellPrice
  const vs = marketPrice > 0 ? Math.round((price / marketPrice - 1) * 100) : 0
  const market = vs === 0 ? 'right at the market price' : `${Math.abs(vs)}% ${vs > 0 ? 'above' : 'below'} the market price`
  const against = diff > 0 ? `so this is ${diff.toLocaleString()} more` : diff < 0 ? `so this is ${(-diff).toLocaleString()} less` : 'so this is the same'
  return `Selling on the market pays ${sellPrice.toLocaleString()} today, ${against}. It is ${market}.`
}

/** A message's clock has run out (the same moment its countdown reads "now"). No clock: never. */
export function expired(until: string | null | undefined, now = Date.now()): boolean {
  return !!until && Number.isFinite(Date.parse(until)) && countdownText(until, now) === 'now'
}

/**
 * The messages for this moment, the most pressing first. Pure: everything comes from the state.
 * A message whose clock has run out is left out at once, also before the wallet is fetched again.
 */
export function buildMessages(s: Pick<AppState, 'wallet' | 'cards' | 'packTypes'>, now = Date.now()): Message[] {
  return allMessages(s).filter((m) => !expired(m.until, now))
}

function allMessages(s: Pick<AppState, 'wallet' | 'cards' | 'packTypes'>): Message[] {
  const w = s.wallet
  const out: Message[] = []
  const offerCard = w.offer ? s.cards.find((c) => c.id === w.offer!.mediaId) : undefined
  if (w.offer && offerCard) {
    out.push({
      id: `offer:${w.offer.mediaId}:${w.offer.expiresAt}`,
      kind: 'offer', icon: '🤝',
      title: `A collector offers 🪙 ${w.offer.price.toLocaleString()} for your ${offerCard.title}`,
      body: offerText(w.offer.price, w.offer.sellPrice, w.offer.marketPrice),
      until: w.offer.expiresAt,
      offer: {
        price: w.offer.price, sellPrice: w.offer.sellPrice, marketPrice: w.offer.marketPrice,
        thumb: offerCard.thumbUrl, lastCopy: offerCard.copies <= 1, cardTitle: offerCard.title,
        treatment: w.offer.treatment, emptiesSlot: Boolean(w.offer.emptiesSlot),
      },
    })
  }
  const wanted = s.cards.filter((c) => c.wanted && c.copies > 0)
  if (wanted.length) {
    out.push({
      id: `wanted:${w.market.tick}`,
      kind: 'wanted', icon: '★',
      title: wanted.length === 1 ? `A collector wants your ${wanted[0].title}` : `Collectors want ${wanted.length} of your cards`,
      // One card: the bonus in coins ("sells for 🪙 31 instead of 24").
      body: wanted.length === 1 && wantedText(wanted[0], wanted[0].treatment)
        ? `Your ${visualFor(wanted[0].treatment).label} copy ${wantedText(wanted[0], wanted[0].treatment)} until the next price update.`
        : 'Wanted cards sell for more until the next price update.',
      until: w.market.nextTickAt, href: '#/market',
    })
  }
  const dealPack = w.deal ? s.packTypes.find((p) => p.id === w.deal!.pack) : undefined
  if (w.deal && dealPack && w.deal.left > 0) {
    out.push({
      id: `deal:${w.deal.endsAt}`,
      kind: 'deal', icon: '🏷',
      title: `Deal: ${dealPack.name} −${Math.round(w.deal.discount * 100)}%`,
      body: `🪙 ${w.deal.price.toLocaleString()} instead of ${w.deal.fullPrice.toLocaleString()} · ${w.deal.left} left`,
      until: w.deal.endsAt, href: '#/packs',
    })
  }
  // The rush is only worth a message while a tag still pays on one of your cards.
  if (w.rush.available && !w.rush.active && rushUseful(s.cards)) {
    out.push({
      id: `rush:${new Date().toDateString()}`,
      kind: 'rush', icon: '⚡',
      title: 'Your tag rush is ready',
      body: `Tags pay ×${w.rush.multiplier} for ${w.rush.minutes} minutes. Once a day.`,
      href: '#/organize',
    })
  }
  return out
}

/** The warning under an offer that takes something away, or '' when a copy stays. */
export function offerWarning(o: { treatment: string; emptiesSlot: boolean; lastCopy: boolean }): string {
  const parts = [
    o.emptiesSlot ? `Accepting empties the ${visualFor(o.treatment).label} slot (−1 version)` : '',
    o.lastCopy ? 'This is your only copy: the card leaves your collection' : '',
  ].filter(Boolean)
  return parts.length ? `${parts.join('. ')}.` : ''
}

/** A tag rush has something to double: one of your cards still pays for its next tag. */
export function rushUseful(cards: Pick<AppState['cards'][number], 'copies' | 'nextTagReward'>[]): boolean {
  return cards.some((c) => c.copies > 0 && c.nextTagReward > 0)
}

export function unseenCount(messages: Message[], seen: ReadonlySet<string>): number {
  return messages.filter((m) => !seen.has(m.id)).length
}

/** How hard the Messages button should ask for attention. */
export interface Attention {
  /** Messages not opened yet. */
  count: number
  /** none: nothing new · new: something waits · urgent: an offer, or something that ends within the hour. */
  level: 'none' | 'new' | 'urgent'
  /** A word for the most pressing new message ("Offer", "Deal"), shown next to the envelope. */
  label: string
  /** When that message ends (ISO), for the clock next to the word. */
  until: string | null
}

const KIND_LABEL: Record<Message['kind'], string> = { offer: 'Offer', wanted: 'Wanted', deal: 'Deal', rush: 'Tag rush' }
export const URGENT_MS = 60 * 60 * 1000

/** A number alone is easy to miss: the button also names what waits and shows how long it stays. */
export function attention(messages: Message[], seen: ReadonlySet<string>, now = Date.now()): Attention {
  const fresh = messages.filter((m) => !seen.has(m.id))
  if (!fresh.length) return { count: 0, level: 'none', label: '', until: null }
  const endsSoon = (m: Message) => {
    const t = m.until ? Date.parse(m.until) : NaN
    return Number.isFinite(t) && t - now <= URGENT_MS
  }
  const urgent = fresh.some((m) => m.kind === 'offer' || endsSoon(m))
  const first = fresh.find((m) => m.kind === 'offer') ?? fresh.find(endsSoon) ?? fresh[0]
  return { count: fresh.length, level: urgent ? 'urgent' : 'new', label: KIND_LABEL[first.kind], until: first.until ?? null }
}

// ---- what was already seen (per browser; losing it only shows a message again) ----

const SEEN_KEY = 'mco.inbox.seen'
const POPPED_KEY = 'mco.inbox.popped'
const TOLD_KEY = 'mco.inbox.told'

function load(key: string): Set<string> {
  try { return new Set(JSON.parse(localStorage.getItem(key) ?? '[]') as string[]) } catch { return new Set() }
}
function save(key: string, ids: Set<string>): void {
  try { localStorage.setItem(key, JSON.stringify([...ids].slice(-60))) } catch { /* private mode */ }
}

// ---- the panel ----

function messageHTML(m: Message, popup = false): string {
  const clock = m.until ? `<span class="inbox-clock">ends in <b data-countdown="${esc(m.until)}">${countdownText(m.until)}</b></span>` : ''
  if (m.offer) {
    return `
    <article class="inbox-msg is-offer" data-msg="${esc(m.id)}">
      ${m.offer.thumb ? `<img class="inbox-thumb" src="${esc(m.offer.thumb)}" alt="">` : `<span class="inbox-icon" aria-hidden="true">${m.icon}</span>`}
      <div class="inbox-text">
        <b>${esc(m.title)}</b>
        <span class="muted">${esc(m.body)}</span>
        ${offerWarning(m.offer) ? `<span class="inbox-warn" style="--tone:${visualFor(m.offer.treatment).color}">${esc(offerWarning(m.offer))}</span>` : ''}
        ${clock}
        <div class="inbox-acts">
          <button class="btn" type="button" data-inbox-offer="1">Take it · 🪙 ${m.offer.price.toLocaleString()}</button>
          <button class="btn btn-ghost" type="button" data-inbox-offer="0">Turn it down</button>
          ${popup ? '<button class="btn btn-ghost" type="button" data-inbox-later>Later</button>' : ''}
        </div>
      </div>
    </article>`
  }
  return `
  <a class="inbox-msg" href="${esc(m.href ?? '#/home')}" data-msg="${esc(m.id)}">
    <span class="inbox-icon" aria-hidden="true">${m.icon}</span>
    <div class="inbox-text"><b>${esc(m.title)}</b><span class="muted">${esc(m.body)}</span>${clock}</div>
  </a>`
}

function newsHTML(news: NewsItem[] | null): string {
  if (news === null) return '<p class="muted inbox-empty">Loading the news…</p>'
  // Only news about your own cards belongs in your messages; the Market page has all of it.
  const mine = news.filter((n) => newsCards(n).mine)
  if (!mine.length) return '<p class="muted inbox-empty">No news about your cards. It comes with the price updates.</p>'
  // Every card name opens that card's panel.
  return `<ul class="inbox-news">${mine.slice(0, 6).map((n) => `<li><b>${newsNamesHTML(n)}:</b> ${esc(n.text)}</li>`).join('')}</ul>`
}

/** Wire the top-bar button, the slide-out panel and the slide-in message for a new offer. */
export function initInbox(button: HTMLButtonElement): void {
  const badge = button.querySelector<HTMLElement>('.inbox-badge')
  const flag = button.querySelector<HTMLElement>('.inbox-flag')
  const panel = document.createElement('aside')
  panel.className = 'inbox'
  panel.setAttribute('aria-label', 'Messages')
  panel.inert = true
  const shade = document.createElement('div')
  shade.className = 'inbox-shade'
  document.body.append(shade, panel)

  let open = false
  let busy = false
  let news: NewsItem[] | null = null
  let popup: HTMLElement | null = null
  let stopClock = () => {}
  let stopFlagClock = () => {}
  let flagKey = ''
  const seen = load(SEEN_KEY)
  const popped = load(POPPED_KEY)
  const told = load(TOLD_KEY)

  const messages = () => buildMessages(getState())

  function renderBadge() {
    const list = messages()
    const a = attention(list, seen)
    const n = a.count
    if (badge) { badge.textContent = String(n); badge.hidden = n === 0 }
    button.classList.toggle('has-new', a.level !== 'none')
    button.classList.toggle('is-urgent', a.level === 'urgent')
    // The word and the clock next to the envelope: what waits, and for how long.
    const key = `${a.level}|${a.label}|${a.until ?? ''}`
    if (flag && key !== flagKey) {
      flagKey = key
      stopFlagClock()
      flag.hidden = a.level === 'none'
      flag.innerHTML = a.level === 'none' ? '' : `${esc(a.label)}${a.until ? ` · <b data-countdown="${esc(a.until)}">${countdownText(a.until)}</b>` : ''}`
      // When the clock runs out the message is over. It is handled once: `messages()` leaves an
      // expired message out, so the redraw below never starts a clock for a time that is past
      // (v0.7.0 did, and ran into itself without end). The wallet is fetched for what comes next.
      stopFlagClock = a.until && !expired(a.until) && Number.isFinite(Date.parse(a.until)) ? startCountdowns(flag, clockRanOut) : () => {}
    }
    button.title = n ? `Messages · ${n} new${a.label ? ` · ${a.label}${a.until ? `, ends in ${countdownText(a.until)}` : ''}` : ''}` : 'Messages'
    button.setAttribute('aria-label', button.title)
    announce(list)
  }

  let refreshing = false
  function clockRanOut() {
    flagKey = ''
    if (refreshing) return
    refreshing = true
    // The fetch tells every subscriber (this button too). If it fails, the button is redrawn from what is known.
    refreshWallet().catch(() => {}).finally(() => { refreshing = false; renderBadge(); if (open && !busy) renderPanel() })
  }

  /** A message that is new since the last look says so once: the envelope rings and a line slides in.
   *  (An offer has its own slide-in with buttons.) */
  function announce(list: Message[]) {
    const fresh = list.filter((m) => !seen.has(m.id) && !told.has(m.id))
    if (!fresh.length) return
    fresh.forEach((m) => told.add(m.id))
    save(TOLD_KEY, told)
    button.classList.remove('is-ringing')
    void button.offsetWidth // restart the animation
    button.classList.add('is-ringing')
    const first = fresh.find((m) => m.kind !== 'offer')
    if (!first || open || overlayOpen()) return
    play('click')
    toast(`${first.icon} ${first.title}`, { action: { label: 'Look', href: first.href ?? '#/home' } })
  }

  function renderPanel() {
    const list = messages()
    panel.innerHTML = `
      <header class="inbox-head"><h2>Messages</h2><button class="icon-btn" type="button" data-inbox-close aria-label="Close messages">✕</button></header>
      <div class="inbox-list">
        ${list.length ? list.map((m) => messageHTML(m)).join('') : '<p class="muted inbox-empty">Nothing needs you right now. Offers from collectors arrive here.</p>'}
      </div>
      <h3 class="inbox-sub">News about your cards</h3>
      ${newsHTML(news)}`
  }

  function setOpen(next: boolean) {
    if (open === next) return
    open = next
    panel.classList.toggle('is-open', open)
    shade.classList.toggle('is-open', open)
    panel.inert = !open
    button.setAttribute('aria-expanded', String(open))
    stopClock()
    if (!open) { button.focus(); return }
    closePopup()
    messages().forEach((m) => seen.add(m.id))
    save(SEEN_KEY, seen)
    renderBadge()
    renderPanel()
    stopClock = startCountdowns(panel)
    panel.querySelector<HTMLElement>('[data-inbox-close]')?.focus()
    fetchMarket().then((m) => { news = m.news.slice().sort((a, b) => Date.parse(b.at) - Date.parse(a.at)); if (open) renderPanel() })
      .catch(() => { news = []; if (open) renderPanel() })
  }

  function closePopup() {
    if (!popup) return
    const el = popup
    popup = null
    el.classList.add('is-leaving')
    setTimeout(() => el.remove(), 250)
  }

  /** A new offer slides in once. "Later" (or ignoring it) leaves it in the message centre. */
  function maybePop() {
    const offer = messages().find((m) => m.kind === 'offer')
    if (!offer || popped.has(offer.id) || open || popup || overlayOpen() || document.querySelector('dialog[open]')) return
    popped.add(offer.id)
    save(POPPED_KEY, popped)
    popup = document.createElement('div')
    popup.className = 'inbox-pop'
    popup.setAttribute('role', 'status')
    popup.innerHTML = messageHTML(offer, true)
    document.body.append(popup)
    const stop = startCountdowns(popup)
    const el = popup
    const watch = setInterval(() => { if (!el.isConnected) { stop(); clearInterval(watch) } }, 2000)
    play('click')
  }

  async function answer(btn: HTMLElement, accept: boolean) {
    if (busy) return
    const offer = messages().find((m) => m.kind === 'offer')?.offer
    // Only an offer that takes something away asks first: the slot empties, or the card leaves the collection.
    if (accept && offer && (offer.emptiesSlot || offer.lastCopy)) {
      busy = true // one question at a time
      const yes = await confirmDialog(offerDialog({ title: offer.cardTitle, treatment: offer.treatment, price: offer.price, emptiesSlot: offer.emptiesSlot, lastCopy: offer.lastCopy }))
      busy = false
      if (!yes) return
    }
    busy = true
    try {
      const r = await answerOffer(accept)
      if (r.accepted) {
        play('coin')
        floatCoins(r.price, btn)
        applySlots(r.slots) // the page underneath stays; the pill shows the version that went
        toast(`You took the offer: 🪙 ${r.price.toLocaleString()}${r.slots && r.slots.delta < 0 ? ' · −1 version' : ''}`, { kind: 'coin' })
      } else {
        toast('You turned the offer down.')
      }
    } catch (err) {
      toast(err instanceof Error && err.message ? err.message : 'The offer is gone.', { kind: 'warn' })
    } finally {
      busy = false
      closePopup()
      await reloadAll().catch(() => {})
      if (open) renderPanel()
      renderBadge()
    }
  }

  button.addEventListener('click', () => setOpen(!open))
  shade.addEventListener('click', () => setOpen(false))
  // Esc closes the panel, unless a dialog is open on top of it (a confirmation, a card panel): that one takes the key.
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && open && !overlayOpen() && !document.querySelector('dialog[open]')) { e.stopPropagation(); setOpen(false) } }, true)
  document.addEventListener('click', (e) => {
    const t = e.target as HTMLElement
    if (!t.closest?.('.inbox, .inbox-pop')) return
    const offer = t.closest<HTMLElement>('[data-inbox-offer]')
    if (offer) return void answer(offer, offer.dataset.inboxOffer === '1')
    if (t.closest('[data-inbox-later]')) return closePopup()
    const named = t.closest<HTMLElement>('[data-news-card]')
    if (named) return openInspect(Number(named.dataset.newsCard)) // on top of the panel, which stays open
    if (t.closest('[data-inbox-close]') || t.closest('a.inbox-msg')) setOpen(false)
  })

  subscribe(() => {
    renderBadge()
    if (open && !busy) { renderPanel() }
    if (popup && !messages().some((m) => m.kind === 'offer')) closePopup() // the offer ran out
    maybePop()
  })
  renderBadge()
  maybePop()
}
