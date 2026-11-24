/**
 * Home: the main action first, then what else is waiting. Two columns (v0.9.0, test round 6 point 1):
 * the main column, and a side bar that holds everything that is claimed: the sealed packs with
 * their Open button, today's check-in and free pack, today's goals, the collector level, the
 * milestones and the getting-started checklist. A claim changes only the side bar, so the main
 * column keeps its height and nothing jumps. On a narrow window the side bar sits under the main
 * column. The page's frame is built once; the main column and the side bar are filled separately.
 *
 * The main column:
 *   1. The hero: the one thing to do now (open a sealed pack, else buy one, else earn coins),
 *      with how many cards are pulled so far.
 *   2. Happening now: a collector's offer (a pointer to Messages), the shop deal, the tag rush,
 *      wanted cards and the next market move, each with its clock. These are the reasons to stay
 *      or to come back. Then "Missing one version": the cards one version short of all three.
 *   3. Your best pulls, as large as the row allows.
 *   4. Tiles: next milestone, packs today, rarest pull, closest target.
 */

import { cardHTML, enableTilt, esc } from '../cards/card.ts'
import { visualFor } from '../cards/treatments.ts'
import { closestTier, fetchGoals, fetchHistory, fetchSets, rewardText } from '../data/economyApi.ts'
import { affordable, completeness, getState, missingCount, missingVersions, packCost, pctText, refreshWallet, sealedTotal, subscribe } from '../data/store.ts'
import type { Card, NextMilestone, PackType, TreatmentInfo } from '../data/types.ts'
import { play } from '../fx/sound.ts'
import { openFromInventory, sealedOf } from '../packs/flow.ts'
import { packArtHTML } from '../packs/opening.ts'
import type { Page } from '../app/router.ts'
import { overlayOpen } from '../ui/overlay.ts'
import type { Chase } from '../data/economyApi.ts'
import { nextGoalText, renderDaily, renderToday } from './chase.ts'
import { renderChecklist } from './checklist.ts'
import { valueChangeText } from '../app/shell.ts'
import { openInspect, wantedText } from './inspect.ts'
import { taggableMore } from './organize.ts'
import { countdownText, startCountdowns } from '../ui/countdown.ts'
import { openHelp } from '../help/index.ts'
import { rushUseful } from '../ui/inbox.ts'

/** The rarest treatment among cards the player owns. */
export function rarestOwned(cards: Card[], treatments: TreatmentInfo[]): TreatmentInfo | null {
  const owned = new Set(cards.filter((c) => c.copies > 0).map((c) => c.treatment))
  return [...treatments].sort((a, b) => b.tier - a.tier).find((t) => owned.has(t.id)) ?? null
}

/** Owned cards, rarest first (the most valuable first within a treatment), for the "best pulls" row. */
export function bestPulls(cards: Card[], treatments: TreatmentInfo[], n: number): Card[] {
  const tier = new Map(treatments.map((t) => [t.id, t.tier]))
  return cards
    .filter((c) => c.copies > 0)
    .sort((a, b) => (tier.get(b.treatment) ?? 0) - (tier.get(a.treatment) ?? 0) || b.value - a.value || b.copies - a.copies)
    .slice(0, n)
}

/** Copies beyond the first of each treatment: what the player can sell without losing a card. */
export function spareCopies(cards: Card[]): number {
  return cards.reduce((n, c) => n + Object.values(c.copiesByTreatment).reduce((m, k) => m + Math.max(0, k - 1), 0), 0)
}

/**
 * "12% to go until 25% · 2 Deluxe packs", or null when every milestone is reached.
 *
 * Milestones are measured against the largest library seen. While the library never shrank that is
 * the completeness on screen. When files went missing (`next.cards` is above `cardsTotal`) the
 * percentage on screen is higher than the milestones' own, so the distance is given in versions:
 * "15 more versions until the 2% milestone (counted against the 300 cards your library had at its largest)".
 */
export function milestoneText(complete: number, next: NextMilestone | null, cardsTotal?: number): string | null {
  if (!next) return null
  const packs = rewardText(next.reward)
  const pays = packs ? ` · ${packs}` : ''
  if (next.versionsNeeded !== undefined && next.cards !== undefined && cardsTotal !== undefined && next.cards > cardsTotal) {
    const n = next.versionsNeeded
    return `${n.toLocaleString()} more ${n === 1 ? 'version' : 'versions'} until the ${Math.round(next.at * 100)}% milestone ` +
      `(counted against the ${next.cards.toLocaleString()} cards your library had at its largest)${pays}`
  }
  const left = Math.max(0, next.at - (next.progress ?? complete)) * 100
  // One decimal unless it is a whole number: "9.9% to go until 10%", never a rounded-up "10% to go until 10%".
  const shown = Number.isInteger(Number(left.toFixed(1))) ? left.toFixed(0) : left.toFixed(1)
  return `${shown}% to go until ${Math.round(next.at * 100)}%${pays}`
}

/** How many owned cards are still at the lowest treatment: what is left to chase once everything is tagged. */
export function stillLowest(cards: Card[], treatments: TreatmentInfo[]): { count: number; name: string } {
  const lowest = [...treatments].sort((a, b) => a.tier - b.tier)[0]
  if (!lowest) return { count: 0, name: '' }
  return { count: cards.filter((c) => c.copies > 0 && c.treatment === lowest.id).length, name: lowest.name }
}

/** Up to `n` pulled cards that lack exactly one of Foil, Holo and Special, the most valuable first, each with the treatment it lacks. */
export function missingOne(cards: Card[], n = 10): { card: Card; missing: string }[] {
  return cards
    .filter((c) => c.copies > 0 && missingVersions(c).length === 1)
    .sort((a, b) => b.value - a.value)
    .slice(0, n)
    .map((card) => ({ card, missing: missingVersions(card)[0] }))
}

/** The line under the progress bar: what is still missing, never just "all done". */
export function heroText(s: {
  owned: number; total: number; untaggedOwned: number; complete: number
  lowest: { count: number; name: string }
  /** Coins missing for the cheapest pack (0 when it is affordable or a sealed pack waits), and that pack's name. */
  short: number; packName: string
  /** Today's check-in is done: tomorrow's brings the next free pack (so the player who is short knows what comes back). */
  tomorrow?: boolean
  /** Versions held and version slots in all (three per card in the folder). */
  versions?: number; versionsTotal?: number
}): string {
  if (s.owned === 0) return 'Every photo and video in your folder is a card waiting in a pack. Open a pack to pull your first ones.'
  const parts = s.total > s.owned ? [`${(s.total - s.owned).toLocaleString()} still to pull.`] : []
  if (s.untaggedOwned) parts.push(`${s.untaggedOwned.toLocaleString()} of your cards need tags, and each tag earns coins.`)
  else if (s.lowest.count) parts.push(`${s.lowest.count.toLocaleString()} of your cards are still ${s.lowest.name}: merge copies or pull a rarer one.`)
  if (s.short > 0) parts.push(`${s.short.toLocaleString()} more coins for a ${s.packName}.`)
  if (s.short > 0 && s.tomorrow) parts.push("Tomorrow's check-in brings a free pack.")
  parts.push(s.versionsTotal
    ? `You hold ${(s.versions ?? 0).toLocaleString()} of ${s.versionsTotal.toLocaleString()} versions (${pctText(s.complete)}).`
    : `You hold ${pctText(s.complete)} of all versions.`)
  return parts.join(' ')
}

export interface MainAction { kind: 'open' | 'buy' | 'fill' | 'tag' | 'tag-more' | 'sell' | 'sell-card' | 'earn'; label: string; href?: string; packId?: string }

/**
 * The one thing to do now: open a sealed pack if you own one; else buy the cheapest pack you can
 * afford (the shop opens with that pack highlighted); else, when cards have missing versions, go to
 * the Missing-versions list; else earn coins: tag the cards you pulled, then add tags to cards that still pay, then
 * sell spare copies on the market, then sell a card you hold (with what your cards are worth);
 * with no card at all: the help article about earning coins.
 */
export function mainAction(s: {
  inventory: Record<string, number>; packs: { id: string; name: string; cost: number }[]; coins: number; untaggedOwned: number
  /** Tagged cards whose next tag still pays. */
  tagMore?: number
  /** Copies the player can sell without losing a card. */
  spares?: number
  /** Cards the player holds, and what they are worth on the market together. */
  cards?: number
  worth?: number
  /** Pulled cards that lack a Foil, Holo or Special. */
  missing?: number
}): MainAction {
  const sealed = s.packs.filter((p) => (s.inventory[p.id] ?? 0) > 0).sort((a, b) => (s.inventory[b.id] ?? 0) - (s.inventory[a.id] ?? 0))[0]
  if (sealed) {
    const n = Object.values(s.inventory).reduce((a, b) => a + b, 0)
    return { kind: 'open', label: `Open a pack · ${n} sealed`, packId: sealed.id }
  }
  const cheapest = [...s.packs].sort((a, b) => a.cost - b.cost)[0]
  if (cheapest && s.coins >= cheapest.cost) return { kind: 'buy', label: `Buy a pack · 🪙 ${cheapest.cost.toLocaleString()}`, href: `#/packs?pack=${cheapest.id}`, packId: cheapest.id }
  if (s.missing) return { kind: 'fill', label: `Fill versions · ${s.missing.toLocaleString()} ${s.missing === 1 ? 'card' : 'cards'}`, href: '#/collection?filter=missing&from=home' }
  if (s.untaggedOwned > 0) return { kind: 'tag', label: `Tag ${s.untaggedOwned} ${s.untaggedOwned === 1 ? 'card' : 'cards'} to earn coins`, href: '#/organize' }
  if (s.tagMore) return { kind: 'tag-more', label: `Add tags to ${s.tagMore} ${s.tagMore === 1 ? 'card' : 'cards'} for coins`, href: '#/organize' }
  if (s.spares) return { kind: 'sell', label: `Sell ${s.spares} spare ${s.spares === 1 ? 'copy' : 'copies'} on the market`, href: '#/market' }
  // Nothing left that pays without giving something up: the cards themselves are worth coins.
  if (s.cards && s.worth) return { kind: 'sell-card', label: `Sell a card on the market · yours are worth 🪙 ${s.worth.toLocaleString()}`, href: '#/market' }
  return { kind: 'earn', label: 'See how to earn coins' } // no href: it opens the help article "How do I earn coins?"
}

/** The line of the side bar's first row: the sealed packs, which every claim adds to. */
export function sealedLine(sealed: number): string {
  return sealed > 0 ? `${sealed.toLocaleString()} sealed ${sealed === 1 ? 'pack' : 'packs'}` : 'No sealed packs'
}

export const homePage: Page = {
  id: 'home',
  title: 'Home',
  icon: '⌂',
  mount(el) {
    let alive = true
    enableTilt(el)

    const keyOf = (s: ReturnType<typeof getState>) =>
      `${s.wallet.coins}:${s.wallet.packsOpened}:${s.cards.length}:${s.wallet.canCheckIn}:${s.wallet.freePack.available}:${sealedTotal()}:${s.wallet.cardsTagged}` +
      `:${s.wallet.slotsFilled}:${s.wallet.slotsTotal}:${s.wallet.market.tick}:${s.wallet.offer?.expiresAt}:${s.wallet.deal?.endsAt}:${s.wallet.deal?.left}:${s.wallet.rush.active}:${s.wallet.rush.available}:${s.wallet.collectionValue}:${s.wallet.collectionChange.change}`
    let key = keyOf(getState())

    let stopClock = () => {}

    // The frame, once. A claim redraws the side bar's own panels in place; the main column is a
    // separate element, so its height does not depend on anything that is claimed.
    el.innerHTML = `
      <div class="home-layout">
        <div class="home-main" data-el="main"></div>
        <aside class="home-side" aria-label="To claim">
          <h2 class="home-side-title">To claim</h2>
          <div class="side-sealed" data-el="sealed" data-tour="home-sealed"></div>
          <div data-el="today"></div>
          <div data-el="daily"></div>
          <div class="home-checklist"></div>
        </aside>
      </div>`
    const main = el.querySelector<HTMLElement>('[data-el="main"]')!

    /** What is happening right now, each with its clock: reasons to act now or to come back. */
    function nowHTML(): string {
      const s = getState()
      const w = s.wallet
      const items: string[] = []
      // A collector's offer lives in Messages (✉ in the top bar). Here it is one line that opens them.
      const offerCard = w.offer ? s.cards.find((c) => c.id === w.offer!.mediaId) : undefined
      if (w.offer && offerCard) {
        items.push(`<button type="button" class="now now-offer" data-act="open-inbox">
          <span class="now-icon" aria-hidden="true">🤝</span>
          <span class="now-text"><b>A collector's offer waits: 🪙 ${w.offer.price.toLocaleString()} for your ${esc(offerCard.title)}</b>
            <span class="muted">Open Messages to take it or turn it down.</span></span>
          <span class="now-clock">ends in <b data-countdown="${esc(w.offer.expiresAt)}">${countdownText(w.offer.expiresAt)}</b></span></button>`)
      }
      const dealPack = w.deal ? s.packTypes.find((p) => p.id === w.deal!.pack) : null
      if (w.deal && dealPack && w.deal.left > 0) {
        items.push(`<a class="now now-deal" href="#/packs">
          <span class="now-icon" aria-hidden="true">🏷</span>
          <span class="now-text"><b>Deal: ${esc(dealPack.name)} −${Math.round(w.deal.discount * 100)}%</b>
            <span class="muted">🪙 ${w.deal.price.toLocaleString()} instead of ${w.deal.fullPrice.toLocaleString()} · ${w.deal.left} left${w.coins < w.deal.price ? ` · you are ${(w.deal.price - w.coins).toLocaleString()} coins short` : ''}</span></span>
          <span class="now-clock">ends in <b data-countdown="${esc(w.deal.endsAt)}">${countdownText(w.deal.endsAt)}</b></span></a>`)
      }
      if (w.rush.active) {
        items.push(`<a class="now now-rush" href="#/organize">
          <span class="now-icon" aria-hidden="true">⚡</span>
          <span class="now-text"><b>Tag rush: every tag pays ×${w.rush.multiplier}</b><span class="muted">Tag now while it runs.</span></span>
          <span class="now-clock"><b data-countdown="${esc(w.rush.endsAt ?? '')}">${countdownText(w.rush.endsAt ?? '')}</b> left</span></a>`)
      } else if (w.rush.available && rushUseful(s.cards)) { // only while a tag still pays: the rush doubles nothing otherwise
        items.push(`<a class="now" href="#/organize">
          <span class="now-icon" aria-hidden="true">⚡</span>
          <span class="now-text"><b>Your tag rush is ready</b><span class="muted">Tags pay ×${w.rush.multiplier} for ${w.rush.minutes} minutes. Once a day.</span></span>
          <span class="now-clock">start it in Organize</span></a>`)
      }
      const wanted = s.cards.filter((c) => c.wanted && c.copies > 0)
      if (wanted.length) {
        items.push(`<a class="now" href="#/market">
          <span class="now-icon" aria-hidden="true">★</span>
          <span class="now-text"><b>${wanted.length === 1 ? `A collector wants your ${esc(wanted[0].title)}` : `Collectors want ${wanted.length} of your cards`}</b>
            <span class="muted">${wanted.length === 1 && wantedText(wanted[0], wanted[0].treatment)
              ? `Your ${visualFor(wanted[0].treatment).label} copy ${esc(wantedText(wanted[0], wanted[0].treatment))} until the market moves.`
              : 'Wanted cards sell for more until the market moves.'}</span></span>
          <span class="now-clock">until <b data-countdown="${esc(w.market.nextTickAt ?? '')}">${countdownText(w.market.nextTickAt ?? '')}</b></span></a>`)
      }
      if (w.market.nextTickAt && w.cardsOwned > 0) {
        const ch = w.collectionChange
        const moved = Math.abs(ch.change) < 0.0005 ? 'It did not move at the last update.'
          : `${ch.change > 0 ? 'Up' : 'Down'} ${valueChangeText(ch.change).slice(2)} (${Math.abs(ch.coins).toLocaleString()} coins) at the last update.`
        items.push(`<a class="now" href="#/market">
          <span class="now-icon" aria-hidden="true">📈</span>
          <span class="now-text"><b>Your collection is worth 🪙 ${w.collectionValue.toLocaleString()}</b>
            <span class="muted">${moved} Every card gets a new price every ${w.market.tickHours} hours.</span></span>
          <span class="now-clock">next in <b data-countdown="${esc(w.market.nextTickAt)}">${countdownText(w.market.nextTickAt)}</b></span></a>`)
      }
      if (!items.length) {
        // Nothing timed is running (no cards yet, or a quiet moment): point at what is waiting.
        const fp = w.freePack
        items.push(fp.available
          ? `<a class="now now-deal" href="#/packs"><span class="now-icon" aria-hidden="true">🎁</span>
              <span class="now-text"><b>Your free pack is ready</b><span class="muted">One free pack every day. It never raises prices.</span></span>
              <span class="now-clock">claim it</span></a>`
          : w.canCheckIn
            ? `<a class="now" href="#/home" data-act="to-today"><span class="now-icon" aria-hidden="true">🔥</span>
                <span class="now-text"><b>Check in today</b><span class="muted">It unlocks your free pack.</span></span>
                <span class="now-clock">today</span></a>`
            : w.market.nextTickAt
              // Nothing timed is active: the next price update is the clock to come back for.
              ? `<a class="now" href="#/market"><span class="now-icon" aria-hidden="true">📈</span>
                  <span class="now-text"><b>The next price update</b><span class="muted">Every card gets a new price every ${w.market.tickHours} hours. Deals and collectors' offers show up here.</span></span>
                  <span class="now-clock">in <b data-countdown="${esc(w.market.nextTickAt)}">${countdownText(w.market.nextTickAt)}</b></span></a>`
              : `<a class="now" href="#/packs"><span class="now-icon" aria-hidden="true">✦</span>
                  <span class="now-text"><b>Every card comes out of a pack</b><span class="muted">Prices, deals and collectors' offers show up here once you own cards.</span></span>
                  <span class="now-clock">to the packs</span></a>`)
      }
      return items.length ? `<section class="home-now" data-tour="home-now" aria-label="Happening now">${items.join('')}</section>` : ''
    }

    /** `side`: also reload the side bar's panels (not after a claim inside one: that panel has just redrawn itself). */
    function render(side = true) {
      const s = getState()
      const c = completeness()
      const untaggedOwned = c.owned - c.tagged
      const action = mainAction({
        inventory: s.wallet.inventory,
        packs: s.packTypes.map((p) => ({ id: p.id, name: p.name, cost: packCost(p) })),
        coins: s.wallet.coins,
        untaggedOwned,
        tagMore: taggableMore(s.cards).length,
        spares: spareCopies(s.cards),
        cards: c.owned,
        worth: s.wallet.collectionValue,
        missing: missingCount(s.cards),
      })
      const chase = missingOne(s.cards)
      const top = rarestOwned(s.cards, s.treatments)
      const pulls = bestPulls(s.cards, s.treatments, 24)
      const ms = milestoneText(s.wallet.completeness, s.wallet.nextMilestone, s.wallet.cardsTotal)
      const std = s.packTypes[0]
      const cheapest = [...s.packTypes].sort((a, b) => packCost(a) - packCost(b))[0]
      const heroPack = (p: PackType) => {
        const n = sealedOf(p.id)
        return `
          <a class="home-pack" href="#/packs?pack=${esc(p.id)}">
            ${packArtHTML(p)}
            <span class="home-pack-cost">${n ? `<b class="home-pack-owned">×${n} sealed</b>` : `🪙 ${packCost(p).toLocaleString()}`}</span>
          </a>`
      }
      main.innerHTML = `
        <section class="home-hero" data-tour="home-hero">
          <div class="home-packs">${s.packTypes.map(heroPack).join('')}</div>
          <div class="home-cta">
            <h2>${c.owned.toLocaleString()} of ${c.total.toLocaleString()} cards pulled</h2>
            <div class="progress"><div class="progress-fill" style="width:${c.pulled * 100}%"></div></div>
            <p class="muted">${esc(heroText({
              owned: c.owned, total: c.total, untaggedOwned, complete: c.complete,
              lowest: stillLowest(s.cards, s.treatments),
              short: cheapest && sealedTotal() === 0 ? Math.max(0, packCost(cheapest) - s.wallet.coins) : 0,
              packName: cheapest?.name ?? 'pack',
              tomorrow: !s.wallet.canCheckIn,
              versions: c.versions, versionsTotal: c.versionsTotal,
            }))}</p>
            <div class="home-actions">
              <div class="home-actions-main">
              ${action.kind === 'open'
                ? `<button class="btn btn-main" type="button" data-act="open" data-pack="${esc(action.packId!)}">${esc(action.label)}</button>`
                : action.href
                  ? `<a class="btn btn-main" href="${action.href}">${esc(action.label)}</a>`
                  : `<button class="btn btn-main" type="button" data-act="earn-help">${esc(action.label)}</button>`}
              </div>
              <div class="home-actions-more">
              ${action.kind !== 'buy' ? `<a class="btn btn-ghost" href="#/packs"${std && affordable(std) ? ` title="Your coins buy ${affordable(std)} ${esc(std.name)}${affordable(std) === 1 ? '' : 's'}"` : ''}>${std && affordable(std) ? `Buy packs · ×${affordable(std)}` : 'Pack shop'}</a>` : ''}
              ${action.kind !== 'tag' && untaggedOwned ? '<a class="btn btn-ghost" href="#/organize" title="Every tag on a card you pulled earns coins">Tag cards for coins</a>' : ''}
              </div>
            </div>
          </div>
        </section>

        ${nowHTML()}

        ${chase.length ? `<section class="panel home-missing" data-tour="home-missing">
          <div class="home-pulls-head"><h3>Missing one version</h3><a href="#/packs">Open packs to fill versions</a></div>
          <div class="home-missing-row">${chase.map(({ card, missing }) => `
            <div class="miss-item" data-inspect="${card.id}" style="--rarity:${visualFor(missing).color}" title="${esc(card.title)}: open this card">
              ${cardHTML(card, { size: 'sm' })}
              <span class="miss-name">${esc(card.title)}</span>
              <span class="miss-what">Missing: <b>${esc(visualFor(missing).label)}</b></span>
            </div>`).join('')}</div>
        </section>` : ''}

        ${pulls.length ? `<section class="panel home-pulls">
          <div class="home-pulls-head"><h3>Your best pulls</h3><a href="#/collection">All ${c.owned.toLocaleString()} cards</a></div>
          <div class="home-pull-row" tabindex="0" role="group" aria-label="Your best pulls. Scroll sideways for more.">${pulls.map((card) =>
            `<div class="home-pull">${cardHTML(card, { size: 'md' })}<span class="vcell-value">🪙 ${card.value.toLocaleString()}</span></div>`).join('')}</div>
        </section>` : ''}

        <section class="home-tiles">
          <a class="tile tile-link" href="#/stats">
            <span class="tile-label">Next milestone</span>
            <b>${ms ? `${Math.round(s.wallet.nextMilestone!.at * 100)}%` : 'All reached'}</b>
            <span class="muted">${ms ? esc(ms) : 'Keep filling versions'}</span>
          </a>
          <a class="tile tile-link" href="#/packs">
            <span class="tile-label">Packs opened today</span>
            <b data-el="packs-today">…</b>
            <span class="muted" data-el="packs-week"></span>
          </a>
          <a class="tile tile-link" href="#/collection${top ? `?filter=${esc(top.id)}` : ''}" ${top ? `style="--rarity:${visualFor(top.id).color}"` : ''}>
            <span class="tile-label">Rarest pull</span>
            <b class="${top ? 'tile-rarity' : ''}">${top ? esc(top.name) : 'None yet'}</b>
            <span class="muted">${top ? 'Your chase so far' : 'Open your first pack'}</span>
          </a>
          <a class="tile tile-link" href="#/stats" data-el="goal-tile">
            <span class="tile-label">Your next goal</span>
            <b>…</b>
            <span class="muted"></span>
          </a>
        </section>`

      stopClock()
      stopClock = startCountdowns(el, () => void refreshWallet().catch(() => {})) // a clock ran out: fetch what comes next
      if (side) renderSide(action)
      else renderSealed(action)
      void fill()
    }

    /**
     * The side bar. Each panel redraws itself in place (the old content stays until the new one
     * is there), so nothing collapses while a claim is on its way.
     */
    function renderSide(action: MainAction) {
      renderSealed(action)
      // After a check-in or a claimed free pack, the hero and the tiles changed too.
      renderToday(el.querySelector<HTMLElement>('[data-el="today"]')!, () => { if (alive) { key = keyOf(getState()); render(false) } })
      const daily = el.querySelector<HTMLElement>('[data-el="daily"]')
      if (daily) void renderDaily(daily, (c) => { showNext(c); if (alive) { key = keyOf(getState()); render(false) } }).then((c) => { if (c) showNext(c) })
      const box = el.querySelector<HTMLElement>('.home-checklist')
      if (box) void renderChecklist(box, () => { if (alive) { key = keyOf(getState()); render(false) } })
    }

    /** The side bar's first row: the sealed packs and the way forward, "Open". Always there, always the same height. */
    function renderSealed(action: MainAction) {
      const sealed = sealedTotal()
      const openPack = action.kind === 'open' ? action.packId : undefined
      el.querySelector<HTMLElement>('[data-el="sealed"]')!.innerHTML = `
        <span class="side-sealed-icon" aria-hidden="true">✦</span>
        <span class="side-sealed-text"><b>${esc(sealedLine(sealed))}</b><span class="muted">${sealed ? 'Every reward you claim lands here.' : 'Rewards pay sealed packs. Claim one below.'}</span></span>
        ${openPack
          ? `<button class="btn" type="button" data-act="open" data-pack="${esc(openPack)}">Open</button>`
          : '<a class="btn btn-ghost" href="#/packs">Shop</a>'}`
    }

    /** The one next goal, from the server: there is always one while any goal is open. */
    function showNext(c: Chase) {
      const tile = el.querySelector<HTMLAnchorElement>('[data-el="goal-tile"]')
      const text = nextGoalText(c.next)
      if (!alive || !tile || !text) return
      tile.dataset.filled = '1'
      tile.querySelector('b')!.textContent = text.big
      tile.querySelector('.muted')!.textContent = text.small
      if (c.next?.href) tile.href = c.next.href
    }

    /** The numbers that need their own requests. Failing quietly leaves the placeholders. */
    async function fill() {
      const [history, goals, sets] = await Promise.allSettled([fetchHistory(), fetchGoals(), fetchSets()])
      if (!alive) return
      if (history.status === 'fulfilled') {
        const h = history.value
        const today = el.querySelector('[data-el="packs-today"]')
        const week = el.querySelector('[data-el="packs-week"]')
        if (today) today.textContent = String(h.packs_today)
        if (week) week.textContent = `${h.packs_this_week} this week · ${h.near_misses_this_week} near ${h.near_misses_this_week === 1 ? 'miss' : 'misses'}`
      }
      const tile = el.querySelector<HTMLElement>('[data-el="goal-tile"]')
      if (!tile || tile.dataset.filled) return // the server already named the next goal
      const big = tile.querySelector('b')!
      const small = tile.querySelector('.muted')!
      const g = goals.status === 'fulfilled' ? goals.value : null
      const openGoal = g && !g.locked ? [...g.goals].filter((x) => !x.claimed).sort((a, b) => b.progress / b.target - a.progress / a.target)[0] : null
      const near = sets.status === 'fulfilled' ? closestTier(sets.value) : null
      if (openGoal) {
        big.textContent = `${openGoal.progress}/${openGoal.target}`
        small.textContent = `${openGoal.title} · ${rewardText(openGoal.reward)}`
      } else if (near) {
        big.textContent = `${near.tier.progress}/${near.set.size}`
        small.textContent = `"${near.set.name}" set in ${visualFor(near.tier.treatment).label} · ${rewardText(near.tier.reward)}`
      } else if (g?.locked) {
        const left = Math.max(0, (g.unlock_after_packs ?? 0) - (g.packs_opened ?? 0))
        big.textContent = `${left} packs`
        small.textContent = 'until weekly goals unlock'
      } else {
        big.textContent = 'Start a set'
        small.textContent = 'Give 5 cards the same tag'
      }
    }

    const rerender = () => { if (alive) { key = keyOf(getState()); render() } }

    el.addEventListener('click', (e) => {
      const t = e.target as HTMLElement
      const open = t.closest<HTMLElement>('[data-act="open"]')
      if (open && !overlayOpen()) {
        const pack = getState().packTypes.find((p) => p.id === open.dataset.pack)
        if (pack) { play('click'); openFromInventory(pack, 1, rerender) }
        return
      }
      if (t.closest('[data-act="earn-help"]')) return void openHelp('earning-coins')
      // The offer line: the offer itself, with its buttons, is in Messages.
      if (t.closest('[data-act="open-inbox"]')) return void document.querySelector<HTMLElement>('#inbox')?.click()
      const miss = t.closest<HTMLElement>('.miss-item')
      if (miss) return openInspect(Number(miss.dataset.inspect), rerender)
      // "Check in today" in the strip: the claim button is on this page, so go to it.
      if (t.closest('[data-act="to-today"]')) {
        e.preventDefault()
        const today = el.querySelector<HTMLElement>('[data-tour="today"]')
        today?.scrollIntoView({ block: 'center', behavior: 'smooth' })
        today?.querySelector<HTMLElement>('[data-act="checkin"]')?.focus({ preventScroll: true })
        return
      }
      const card = t.closest<HTMLElement>('.home-pull-row .card')
      if (card) openInspect(Number(card.dataset.cardId), rerender)
    })
    el.addEventListener('keydown', (e) => {
      const card = (e.target as HTMLElement).closest<HTMLElement>('.home-pull-row .card, .miss-item .card')
      if (card && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openInspect(Number(card.dataset.cardId), rerender) }
    })

    // Follow the balance, prices and inventory, but never rebuild under an open dialog or overlay.
    const off = subscribe((s) => {
      const next = keyOf(s)
      if (next === key || overlayOpen() || document.querySelector('dialog[open]')) return
      key = next
      render()
    })
    render()
    return () => { alive = false; off(); stopClock() }
  },
}
