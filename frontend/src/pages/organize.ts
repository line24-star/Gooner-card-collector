/**
 * Organize mode: one big card, a tag input and nine quick-tag chips, driven from the keyboard.
 * Enter applies the typed tag, 1-9 apply a chip, → / Tab skip, Esc returns to the Collection.
 * A click on the card shows it full screen. A click on one of the card's tags removes that tag
 * and takes back the coins it paid (to correct a typing error).
 *
 * Two rounds. First the cards without a tag: one tag and the next card comes. When none are left,
 * the player can go on with cards that already have tags, in a batch of 10, 20 or 50 picked at
 * random: every extra tag still pays (less each time, down to 1 coin), and the card stays until
 * the player moves on. On the screen between two batches the tag rush comes first.
 * Coins and the balance always come from server responses.
 *
 * The tagging pause (v0.9.0): after a run of very fast quick-key tags the server pays nothing for
 * a few seconds unless the tag is typed. The page says so, locks the quick tags with a countdown,
 * and tells the server which tags were typed (`typed`).
 */

import { cardHTML, enableTilt, esc } from '../cards/card.ts'
import { addTag, ApiError, fetchTags, removeTag, startRushApi } from '../data/api.ts'
import { gainedCoins, sample } from '../data/bulk.ts'
import { getState, refreshWallet, reloadAll, replaceCard, subscribe } from '../data/store.ts'
import type { Card, Wallet } from '../data/types.ts'
import { floatCoins } from '../fx/effects.ts'
import { play } from '../fx/sound.ts'
import { navigate, type Page } from '../app/router.ts'
import { errorMessage, normalizeTagInput, paysText, tagChipsHTML } from './inspect.ts'
import { firstVisitTour } from '../help/index.ts'
import { countdownText, startCountdowns } from '../ui/countdown.ts'
import { openLightbox } from '../ui/lightbox.ts'
import { overlayOpen } from '../ui/overlay.ts'
import { attachTagSuggest } from '../ui/tagInput.ts'
import { NO_PAUSE, pauseFrom, pauseText, quickLocked, removedTagText, type TagPause } from '../ui/tagPause.ts'

/** How many quick tags are offered, on the keys 1 to 9 (v0.9.0: was 6). */
export const QUICK_TAGS = 9

/**
 * Untagged cards only. Owned cards first, highest treatment tier first (they pay the most),
 * then un-pulled cards. Ties are ordered by title.
 */
export function nextOrganizeQueue(cards: Card[], treatments: { id: string; tier: number }[] = getState().treatments): Card[] {
  const tier = (id: string) => treatments.find((t) => t.id === id)?.tier ?? 0
  const rank = (c: Card): number => {
    const ids = Object.keys(c.owned ?? {})
    return ids.length ? Math.max(...ids.map(tier)) : -1
  }
  return cards
    .filter((c) => c.tags.length === 0)
    .sort((a, b) => rank(b) - rank(a) || a.title.localeCompare(b.title))
}

/** The batch sizes offered for a round over cards that already have tags. */
export const BATCH_SIZES = [10, 20, 50] as const

/** Cards that already have a tag and still pay for another one. */
export function taggableMore(cards: Card[]): Card[] {
  return cards.filter((c) => c.tags.length > 0 && c.nextTagReward > 0)
}

/** A batch of `size` such cards, picked at random each time (v0.9.0: it was always the same best-paying cards). */
export function moreBatch(cards: Card[], size: number, rand: () => number = Math.random): Card[] {
  return sample(taggableMore(cards), size, rand)
}

/** The quick tags for a card: the most used tags the card does not have yet, at most nine. */
export function quickTags(chips: string[], card: Pick<Card, 'tags'> | undefined): string[] {
  return chips.filter((n) => !card || !card.tags.includes(n)).slice(0, QUICK_TAGS)
}

/** Where the tag rush stands: running, ready to start, coming back at a known time, or not in the game. */
export function rushState(r: Wallet['rush']): 'on' | 'ready' | 'later' | 'off' {
  if (r.minutes <= 0) return 'off'
  return r.active ? 'on' : r.available ? 'ready' : 'later'
}

/** "The next tag rush is ready in 5 h 12 min", or that it comes tomorrow when the server names no time. */
export function rushWaitText(r: Pick<Wallet['rush'], 'availableAt'>, now = Date.now()): string {
  const at = r.availableAt ? Date.parse(r.availableAt) : NaN
  if (!Number.isFinite(at) || at <= now) return 'The next tag rush comes tomorrow.'
  return `The next tag rush is ready in ${countdownText(r.availableAt ?? null, now)}.`
}

export { paysText } from './inspect.ts'

export const organizePage: Page = {
  id: 'organize',
  title: 'Organize',
  icon: '✎',
  mount(el) {
    if (getState().mode !== 'live') {
      el.innerHTML = '<p class="muted organize-note">Available once your media is imported.</p>'
      return
    }
    let more = false // the round over cards that already have tags
    let queue = nextOrganizeQueue(getState().cards)
    let session = 0
    let batchDone = 0 // cards finished in the batch that just ended
    let busy = false
    let message = ''
    let chips: string[] = []
    let alive = true
    let pause: TagPause = NO_PAUSE
    let unpaid = false // the tag just sent landed in the pause
    let pauseTimer: ReturnType<typeof setInterval> | undefined

    el.innerHTML = `
      <div class="organize">
        <div class="organize-stage"></div>
        <div class="organize-panel">
          <div class="organize-bar" data-tour="organize-counter">
            <span><b data-el="remaining">0</b> <span data-el="remaining-label">untagged left</span></span>
            <span><b data-el="session">+0</b> coins this session</span>
          </div>
          <div class="organize-rush" data-el="rush" data-tour="organize-rush"></div>
          <div class="organize-tags" data-el="tags" role="group" aria-label="Tags on this card. Click one to remove it."></div>
          <p class="organize-pays" data-el="pays"></p>
          <div class="organize-field">
            <input class="input organize-input" data-tour="organize-input" maxlength="60" autocomplete="off"
              aria-label="Tag this card" placeholder="Type a tag, then Enter">
          </div>
          <p class="organize-pause" data-el="pause" role="status" aria-live="polite" hidden></p>
          <div class="organize-chips" data-tour="organize-chips" role="group" aria-label="Quick tags"></div>
          <div class="organize-actions">
            <button class="btn btn-ghost" data-tour="organize-skip" data-act="skip" type="button">Skip · → / Tab</button>
            <a class="btn btn-ghost" href="#/collection">Esc · Done</a>
          </div>
          <p class="inspect-msg" role="status" aria-live="polite" data-el="msg"></p>
          <p class="muted organize-keys">Enter apply · 1–${QUICK_TAGS} quick tag · → or Tab <span data-el="skip-word">skip</span> · click the card for full screen · click a tag to remove it · Esc back</p>
          <div class="organize-next" aria-label="Up next"></div>
        </div>
      </div>`
    const q = <T extends HTMLElement>(s: string) => el.querySelector<T>(s)!
    const root = q('.organize')
    const stage = q('.organize-stage')
    const input = q<HTMLInputElement>('.organize-input')
    enableTilt(stage)

    /**
     * The tag rush on the screen between two batches: the first and largest thing there while it
     * runs or can be started; when it cannot, one line that says when it can.
     */
    function doneRushHTML(left: number): string {
      const r = getState().wallet.rush
      const state = rushState(r)
      if (state === 'off') return ''
      if (state === 'on') {
        return `<div class="rush-hero is-on" role="status">
          <b class="rush-hero-title">⚡ The tag rush is running</b>
          <span class="rush-hero-clock"><b data-countdown="${esc(r.endsAt ?? '')}">${countdownText(r.endsAt ?? '')}</b> left</span>
          <span>Every tag pays ×${r.multiplier}. ${left ? 'Pick a batch below and tag while it runs.' : 'No card pays for another tag right now.'}</span>
        </div>`
      }
      if (state === 'ready' && left) {
        return `<div class="rush-hero is-ready">
          <b class="rush-hero-title">⚡ Your tag rush is ready</b>
          <span>Tags pay ×${r.multiplier} for ${r.minutes} minutes. Start it, then pick a batch.</span>
          <button class="btn rush-btn rush-hero-btn" type="button" data-act="rush" data-focus>⚡ Start the tag rush</button>
        </div>`
      }
      if (state === 'ready') return ''
      return `<p class="rush-later muted">⚡ ${r.availableAt
        ? `The next tag rush is ready in <b data-countdown="${esc(r.availableAt)}">${countdownText(r.availableAt)}</b>.`
        : esc(rushWaitText(r))}</p>`
    }

    /** Nothing in the queue: say so in the middle of the page, and offer what there is to do next. */
    function doneHTML(): string {
      const cards = getState().cards
      if (!cards.length) {
        return `<div class="organize-done"><h2>No cards to tag yet</h2>
          <p>You can tag the cards you pulled. Open a pack to pull your first ones.</p>
          <div class="organize-done-acts"><a class="btn" href="#/packs">Open packs</a></div></div>`
      }
      const left = taggableMore(cards).length
      const rush = doneRushHTML(left)
      const rushFirst = rushState(getState().wallet.rush) === 'ready' && left > 0 // its button is the main one
      return `<div class="organize-done">
        ${rush}
        <h2>${batchDone ? `Batch done: ${batchDone} ${batchDone === 1 ? 'card' : 'cards'}` : 'Every card you pulled has a tag'}</h2>
        ${left ? `<p>Keep going with cards that already have tags. Every extra tag still pays: less each time, down to 1 coin.
            <b>${left.toLocaleString()}</b> ${left === 1 ? 'card still pays' : 'cards still pay'}. Each batch is a new random pick.</p>
          <div class="organize-done-acts" role="group" aria-label="Tag more cards">
            ${BATCH_SIZES.map((n, i) => `<button class="btn${i || rushFirst ? ' btn-ghost' : ''}" type="button" data-batch="${n}"${i === 0 && !rushFirst ? ' data-focus' : ''}>${Math.min(n, left) === n ? `${n} cards` : `All ${left}`}</button>`)
              .filter((_, i) => i === 0 || BATCH_SIZES[i - 1] < left).join('')}
          </div>` : '<p>Every tag that pays is placed. New cards come out of packs.</p>'}
        <div class="organize-done-acts">
          <a class="btn${left ? ' btn-ghost' : ''}" href="#/packs">Open packs</a>
          <a class="btn btn-ghost" href="#/market">See the market</a>
        </div>
      </div>`
    }

    function rushHTML(): string {
      const r = getState().wallet.rush
      const state = rushState(r)
      if (state === 'off') return ''
      if (state === 'on') return `<span class="rush is-on">⚡ Tag rush: every tag pays ×${r.multiplier} · <b data-countdown="${esc(r.endsAt ?? '')}">${countdownText(r.endsAt ?? '')}</b> left</span>`
      if (state === 'ready') return `<button class="btn rush-btn" type="button" data-act="rush">⚡ Start the tag rush</button>
        <span class="muted">Tags pay ×${r.multiplier} for ${r.minutes} minutes. Once a day.</span>`
      return `<span class="muted">⚡ ${r.availableAt
        ? `The next tag rush is ready in <b data-countdown="${esc(r.availableAt)}">${countdownText(r.availableAt)}</b>.`
        : esc(rushWaitText(r))}</span>`
    }

    /** The pause line and the lock on the quick tags, from the clock. Runs on a timer while a pause is on. */
    function drawPause() {
      const locked = quickLocked(pause)
      const line = q('[data-el="pause"]')
      const text = pauseText(pause, Date.now(), unpaid)
      line.textContent = text
      line.hidden = !text
      q('.organize-chips').classList.toggle('is-locked', locked)
      el.querySelectorAll<HTMLButtonElement>('[data-chip]').forEach((b) => { b.disabled = locked || !queue[0] })
      if (!locked && pauseTimer !== undefined) {
        clearInterval(pauseTimer)
        pauseTimer = undefined
        if (!text) unpaid = false
      }
    }

    function setPause(next: TagPause, paidNothing: boolean) {
      pause = next
      unpaid = paidNothing
      if (quickLocked(pause) && pauseTimer === undefined) pauseTimer = setInterval(() => { if (alive) drawPause() }, 250)
      drawPause()
    }

    function render() {
      const c = queue[0]
      root.classList.toggle('is-done', !c)
      q('[data-el="remaining"]').textContent = String(queue.length)
      q('[data-el="remaining-label"]').textContent = more ? 'left in this batch' : 'untagged left'
      q('[data-el="session"]').textContent = `${session < 0 ? '−' : '+'}${Math.abs(session)}`
      q('[data-el="msg"]').textContent = message
      q('[data-el="rush"]').innerHTML = rushHTML()
      q('[data-el="skip-word"]').textContent = more ? 'next card' : 'skip'
      q('[data-act="skip"]').textContent = more ? 'Next card · → / Tab' : 'Skip · → / Tab'
      q('[data-el="tags"]').innerHTML = c && c.tags.length ? tagChipsHTML(c) : ''
      q('[data-el="pays"]').textContent = c ? paysText(c.nextTagReward) : ''
      const key = c ? `${c.id}:${c.treatment}` : 'done'
      if (stage.dataset.key !== key || !c) {
        stage.dataset.key = key
        stage.innerHTML = c ? cardHTML(c, { size: 'xl' }) : doneHTML()
        stage.querySelector<HTMLElement>('.card')?.setAttribute('title', 'Click for the full-screen view')
      } else {
        // The same card after one more tag: only the text on its face changes, so a video keeps playing.
        const face = stage.querySelector<HTMLElement>('.card')
        const fresh = document.createElement('div')
        fresh.innerHTML = cardHTML(c, { size: 'xl' })
        for (const part of ['.card-head', '.card-foot']) {
          const now = face?.querySelector(part)
          const next = fresh.querySelector(part)
          if (now && next) now.replaceWith(next)
        }
      }
      input.disabled = !c
      q('.organize-next').innerHTML = queue.slice(1, 5).map((n) => `<div class="organize-thumb" title="${esc(n.title)}">${n.thumbUrl ? `<img src="${esc(n.thumbUrl)}" alt="" loading="lazy">` : ''}</div>`).join('')
      q('.organize-chips').innerHTML = quickTags(chips, c).map((n, i) =>
        `<button class="chip" type="button" data-chip="${esc(n)}" ${c ? '' : 'disabled'}><kbd>${i + 1}</kbd> ${esc(n)}</button>`).join('')
      drawPause()
      if (!c) stage.querySelector<HTMLElement>('[data-focus]')?.focus()
    }

    async function loadTags() {
      try {
        const tags = await fetchTags()
        // More than nine: the tags the card already has are left out, and nine should still be offered.
        chips = [...tags].sort((a, b) => b.count - a.count || a.name.localeCompare(b.name)).slice(0, QUICK_TAGS * 2).map((t) => t.name)
        if (alive) render()
      } catch { /* chips are optional */ }
    }

    /** `typed`: the tag was typed into the field and sent with Enter. A quick key, a chip or a suggestion is not typed. */
    async function apply(raw: string, typed: boolean) {
      const c = queue[0]
      if (busy || !c) return
      const name = normalizeTagInput(raw)
      if (!name) { message = 'Tags need 1 to 40 characters'; return render() }
      if (c.tags.includes(name)) { message = `This card already has "${name}".`; input.value = ''; return render() }
      // During the pause a quick tag would be added and pay nothing: it is not sent. The line above the quick tags says why.
      if (!typed && quickLocked(pause)) { message = ''; input.focus(); return drawPause() }
      busy = true
      try {
        const r = await addTag(c.id, name, undefined, typed)
        replaceCard(r.card)
        const gained = gainedCoins(r)
        session += gained
        if (gained > 0) { floatCoins(gained, stage); play('coin') }
        setPause(pauseFrom(r), Boolean(r.paused))
        // First round: one tag and on to the next card. Tag-more round: the card stays for more tags.
        queue = more ? [r.card, ...queue.slice(1)] : queue.filter((x) => x.id !== c.id)
        message = ''
        input.value = ''
        await refreshWallet().catch(() => {})
        void loadTags()
      } catch (e) {
        message = errorMessage(e)
      } finally {
        busy = false
        if (alive) { render(); if (queue[0]) { input.focus(); suggest.refresh() } }
      }
    }

    /** A click on one of the card's tags: the tag goes, and the coins it paid are taken back. */
    async function remove(tagId: number) {
      const c = queue[0]
      const i = c ? c.tagIds.indexOf(tagId) : -1
      if (busy || !c || i < 0) return
      const name = c.tags[i]
      busy = true
      try {
        const r = await removeTag(c.id, tagId)
        if (r) {
          replaceCard(r.card)
          queue = [r.card, ...queue.slice(1)]
          session -= r.refunded
          if (r.refunded > 0) { floatCoins(-r.refunded, stage); play('click') }
          message = removedTagText(name, r.refunded)
        }
        await refreshWallet().catch(() => {})
        void loadTags()
      } catch (e) {
        message = errorMessage(e)
      } finally {
        busy = false
        if (alive) { render(); if (queue[0]) input.focus() }
      }
    }

    function skip() {
      if (busy || !queue.length) return
      if (more) { // "Next card": this one is finished
        queue = queue.slice(1)
        batchDone++
        if (!queue.length) more = false
      } else {
        if (queue.length < 2) return
        queue = [...queue.slice(1), queue[0]]
      }
      message = ''
      input.value = ''
      render()
      if (queue[0]) input.focus()
    }

    function startBatch(size: number) {
      const batch = moreBatch(getState().cards, size)
      if (!batch.length) return
      more = true
      batchDone = 0
      queue = batch
      message = ''
      render()
      input.focus()
    }

    async function startRush() {
      if (busy) return
      busy = true
      try {
        await startRushApi()
        play('reveal-1')
        await reloadAll() // every card's "next tag pays" is higher now
        const fresh = new Map(getState().cards.map((c) => [c.id, c]))
        queue = queue.map((c) => fresh.get(c.id) ?? c)
        message = ''
      } catch (e) {
        message = e instanceof ApiError && e.status === 409 ? 'The tag rush cannot be started right now.' : errorMessage(e)
        await refreshWallet().catch(() => {})
      } finally {
        busy = false
        if (alive) { render(); if (queue[0]) input.focus() }
      }
    }

    const suggest = attachTagSuggest(input, { mediaId: () => queue[0]?.id, onPick: (name) => void apply(name, false), typedOnly: true })

    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || overlayOpen() || document.querySelector('dialog[open]')) return
      const t = e.target as HTMLElement
      const inInput = t === input
      if (!inInput && t.closest('input, textarea, dialog')) return
      if (e.key === 'Escape') { e.preventDefault(); return navigate('collection') }
      if (!queue.length) return // the "what next" screen: its buttons take the keys
      if (e.key === 'Enter' && inInput) { e.preventDefault(); return void apply(input.value, true) }
      if (e.key === 'Tab') { e.preventDefault(); return skip() }
      if (inInput && input.value !== '') return // typing: leave everything else alone
      if (e.key === 'ArrowRight') { e.preventDefault(); return skip() }
      const chip = /^[1-9]$/.test(e.key) ? quickTags(chips, queue[0])[Number(e.key) - 1] : undefined
      if (chip) { e.preventDefault(); void apply(chip, false) }
    }
    window.addEventListener('keydown', onKey)
    el.addEventListener('click', (e) => {
      const t = e.target as HTMLElement
      const chip = t.closest<HTMLButtonElement>('[data-chip]')
      if (chip) return void (chip.disabled ? undefined : apply(chip.dataset.chip!, false))
      const rm = t.closest<HTMLElement>('[data-remove]')
      if (rm) return void remove(Number(rm.dataset.remove))
      const batch = t.closest<HTMLElement>('[data-batch]')
      if (batch) return startBatch(Number(batch.dataset.batch))
      if (t.closest('[data-act="rush"]')) return void startRush()
      if (t.closest('[data-act="skip"]')) return skip()
      // The card itself: the full-screen view. It is a dialog, so the page's keys rest while it is open.
      if (queue[0] && t.closest('.organize-stage .card')) openLightbox(queue[0])
    })
    stage.addEventListener('keydown', (e) => {
      const t = e.target as HTMLElement
      if ((e.key === 'Enter' || e.key === ' ') && t.matches('.card') && queue[0]) { e.preventDefault(); e.stopPropagation(); openLightbox(queue[0]) }
    })

    // The rush starts and ends while the page is open: keep its line and the clock current.
    let rushKey = ''
    const off = subscribe((s) => {
      const key = `${s.wallet.rush.active}:${s.wallet.rush.available}:${s.wallet.rush.endsAt}:${s.wallet.rush.availableAt}`
      if (!alive || key === rushKey) return
      rushKey = key
      q('[data-el="rush"]').innerHTML = rushHTML()
      if (!queue[0] && !busy) render() // the "what next" screen shows the rush too
    })
    const stopClock = startCountdowns(el, () => void reloadAll().then(() => {
      if (!alive) return
      const fresh = new Map(getState().cards.map((c) => [c.id, c]))
      queue = queue.map((c) => fresh.get(c.id) ?? c)
      render()
    }).catch(() => {}))

    render()
    if (queue[0]) input.focus()
    void loadTags()
    // The tour points at the tag field and the quick tags: without a card to tag they are not on screen.
    if (queue[0]) void firstVisitTour('organize')
    return () => {
      alive = false; off(); stopClock(); suggest.destroy(); window.removeEventListener('keydown', onKey)
      if (pauseTimer !== undefined) clearInterval(pauseTimer)
    }
  },
}
