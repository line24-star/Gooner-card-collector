/**
 * Full-screen pack opening.
 *
 *   pick one of three sealed packs (a ritual only: the pick does not change the cards. Nothing is
 *   opened until a pack is picked, so closing here keeps the pack sealed) ──► the server opens it
 *   ──► torn ──► face-down cards, held for 800 ms before the first one can be turned. Some have a
 *     slowly pulsing green edge: the server's HINT that the card fills an empty Foil, Holo or
 *     Special slot. The hint can be wrong on purpose (v0.9.0, D6): most cards that fill a slot
 *     glow, some do not, and some glow that fill nothing. Every back glows a little more the
 *     longer the run of packs without a Holo or Special is (looks only, no number, no odds).
 *     Under a back: the card's print tier. Pointing at a back lifts it with a neutral glow (it says nothing about the treatment)
 *     ──flip each / Reveal all──► a card that is clicked glows for 400 to 800 ms, then turns.
 *     The same row, face up: its value (counting up, with coins that grow with the value), what
 *     the pull did ("Fills Foil slot", "Already owned — slot still empty"), and a glowing border
 *     in the colour of that status
 *     ──► above the row the version line, from the start: it counts up as cards are turned
 *     ("No new versions yet" → "+1 version" → "+2 versions"; "No new versions" when all are up
 *     and none filled a slot), never ahead of them, with the slots that filled; under the row the
 *     buttons change to "Open another" / "Next pack" and "Close"
 *
 * There is no separate results screen: the cards stay where they were flipped. The cards are as
 * large as the screen allows (their width comes from the viewport and the number of cards). A
 * flipped card can be clicked: the card panel opens on top of the overlay (the price history is
 * there, and a click on its art is the full-screen view). Closing it leaves the overlay as it was.
 *
 * A card that fills a slot has its own moment when it turns: a sound of its own, a short lock-in
 * ring, a floating "+1 version", and the version pill in the overlay's bar (and the one in the top
 * bar under it) goes up by one on that same frame, never before.
 *
 * Reduced motion: the floors stay, shorter (300 ms and 150 ms); the hint is a static green edge
 * instead of a pulse; every line, number and sound is the same.
 *
 * Packs come out of the inventory: `nextPack` opens a sealed one (the caller decides whether it
 * may buy one when none is left), and `sealed()` says how many are left for the buttons. Feedback grows with the treatment's revealLevel when a card is
 * flipped by hand (flip sound → foil chime → holo flash → special flash + shake + fanfare);
 * "Reveal all" flips quietly, without flashes or shakes.
 *
 * While the overlay is open the page behind it is inert (keys and focus stay in the overlay), and
 * closing it cancels every queued flip and sound.
 */

import './opening.css'
import { cardHTML, enableTilt, esc } from '../cards/card.ts'
import { visualFor } from '../cards/treatments.ts'
import type { PackType, PulledCard } from '../data/types.ts'
import { celebrate, stopCelebrations, type RarityLevel } from '../fx/burst.ts'
import { coinBurst, countTo, floatText, shake } from '../fx/effects.ts'
import { play, type Sfx } from '../fx/sound.ts'
import { bindOverlay } from '../ui/overlay.ts'
import { toastMilestones } from '../ui/toast.ts'

export interface OpeningOptions {
  pack: PackType
  /** Opens the next pack (a sealed one, or a newly bought one). Throws (ApiError 402 = not enough coins, 409 = none sealed). null = a call is already pending. */
  nextPack: () => Promise<PulledCard[] | null>
  /** Sealed packs of this kind left in the inventory. */
  sealed: () => number
  /** How many packs to open in a row (Open 1 / Open 10). */
  count: number
  /** Today's price of one more pack, and the current balance (both change as packs are opened). */
  price: () => number
  coins: () => number
  /** How big the screen effects are for this pack: 1 for the cheapest, more for the dearer packs. */
  fxIntensity?: number
  /** Copies of one card needed to fuse (for the prompt after a pack). */
  fuseCopies?: number
  /**
   * Merge a card's copies right from the results. Resolves to the id of the treatment it became, or
   * null when the player said no in the confirmation (a merge that empties a slot asks first).
   * Rejects with the server's reason.
   */
  fuse?: (id: number, treatment: string) => Promise<string | null>
  /**
   * Milestones the pack just opened reached; called once per pack, right after nextPack. v0.9.0:
   * nothing is paid, they wait to be claimed. They are announced when the last card is face up,
   * never while the cards are face down.
   */
  milestones?: () => unknown[]
  /**
   * Packs opened in a row without a Holo or Special BEFORE the pack about to be opened (read when
   * a pack is picked). It only sets how strongly the backs glow: a look, not odds, and never a number.
   */
  dryStreak?: () => number
  /** The versions held and the slots there are, as the pill shows them (the cards still face down are not in it). */
  versions?: () => { filled: number; total: number }
  /** A card that fills a slot was turned: count it now. */
  revealVersion?: (id: number, treatment: string) => void
  /** Count every version that is still face down (the overlay closes). */
  settleVersions?: () => void
  /** Open the card panel on top of the overlay. `price`: with the price column in view. */
  inspect?: (id: number, price?: boolean) => void
  /** "sells for 🪙 31 instead of 24" for a wanted card, or '' (for the prompt after a pack). */
  wantedText?: (card: PulledCard['card']) => string
  /** Today's free pack: 'ready' to claim, or null. */
  freePack?: () => 'ready' | null
  /** The treatment one step above this one (for the near-miss line), or null at the top. */
  nextTreatment?: (id: string) => string | null
  /** Display name of a treatment id, e.g. "Foil". */
  treatmentName?: (id: string) => string
  onClose: () => void
}

export function packArtHTML(pack: PackType, extraClass = ''): string {
  return `
  <div class="pack ${extraClass}" style="--hue:${pack.hue}">
    <div class="pack-top"></div>
    <div class="pack-body">
      <div class="pack-emblem">✦</div>
      <div class="pack-name">${esc(pack.name)}</div>
      <div class="pack-size">${pack.size} cards</div>
    </div>
  </div>`
}

/** Cards per row in the reveal: one row up to 5 cards, two rows above that. */
export function revealCols(count: number): number {
  return count <= 5 ? Math.max(1, count) : Math.ceil(count / 2)
}

export interface PullSummary { fresh: number; better: number; versions: number; normals: number; upgraded: number; duplicates: number; coins: number; value: number }

/** A first Normal copy of a card held only in a rarer treatment: not a duplicate, and not a version (Normal is the base). */
const firstNormal = (p: PulledCard) => p.outcome === 'version' && p.card.treatment === 'normal'

/** What a pack did, counted per outcome. `value` is what all its cards are worth on the market. */
export function summarize(pulls: PulledCard[]): PullSummary {
  const s: PullSummary = { fresh: 0, better: 0, versions: 0, normals: 0, upgraded: 0, duplicates: 0, coins: 0, value: 0 }
  for (const p of pulls) {
    s.value += p.value
    if (p.outcome === 'new') s.fresh++
    else if (p.outcome === 'better') s.better++
    else if (firstNormal(p)) s.normals++
    else if (p.outcome === 'version') s.versions++
    else if (p.outcome === 'upgraded') s.upgraded++
    else { s.duplicates++; s.coins += p.coins }
  }
  return s
}

/** One line for the whole pack, e.g. "3 new cards · 1 rarer copy · 2 duplicates · worth 🪙 40 together". */
export function summaryText(s: PullSummary): string {
  const n = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`
  const parts: string[] = []
  if (s.fresh) parts.push(n(s.fresh, 'new card', 'new cards'))
  if (s.better) parts.push(n(s.better, 'rarer copy', 'rarer copies'))
  if (s.versions) parts.push(n(s.versions, 'new version', 'new versions'))
  if (s.normals) parts.push(n(s.normals, 'first Normal copy', 'first Normal copies'))
  if (s.upgraded) parts.push(n(s.upgraded, 'upgrade', 'upgrades'))
  if (s.duplicates) parts.push(n(s.duplicates, 'duplicate', 'duplicates') + (s.coins ? ` (+${s.coins} coins)` : ''))
  if (s.value) parts.push(`worth 🪙 ${s.value.toLocaleString()} together`)
  return parts.join(' · ')
}

/**
 * The line under one flipped card: what this pull did for the collection.
 * A duplicate is a card you already own in this treatment: it is kept as an extra copy, to sell or fuse.
 * A treatment you held no copy of is not a duplicate, even when you own a rarer one: it is a new version
 * (a Foil, Holo or Special), or a first Normal copy (Normal is the base, not a version).
 */
export function pullNote(p: PulledCard, treatmentName: (id: string) => string = (id) => id): string {
  // v0.9.0 (spec 2): the slot impact is part of the line. The server names the treatment shown.
  const slot = p.fillsSlot ? `Fills ${p.treatmentName || treatmentName(p.card.treatment)} slot` : ''
  if (p.outcome === 'new') return slot ? `New card · ${slot}` : 'New card'
  if (slot) return slot
  if (p.outcome === 'better') return `Rarer copy: ${treatmentName(p.card.treatment)}`
  if (firstNormal(p)) return `First ${treatmentName(p.card.treatment)} copy`
  if (p.outcome === 'version') return `New version: ${treatmentName(p.card.treatment)}`
  if (p.outcome === 'upgraded') return `Duplicate → upgraded to ${treatmentName(p.card.treatment)}`
  // "Already owned": this Foil or Holo was held already while a higher slot of the card is still empty.
  // Not on a Normal duplicate: most duplicates are Normal, and the line on every one of them says nothing.
  if (p.alreadyOwned && p.card.treatment !== 'normal') return p.coins > 0 ? `${ALREADY_OWNED} · +${p.coins} coins` : ALREADY_OWNED
  return p.coins > 0 ? `Duplicate · +${p.coins} coins` : 'Duplicate: an extra copy to sell or merge'
}

/** The near-miss wording of the spec: the treatment was held, a higher slot of the card is not. */
export const ALREADY_OWNED = 'Already owned — slot still empty'

/**
 * The version line above the row: "+2 versions", "+1 version" or "No new versions".
 * `done` false: cards are still face down, so nothing is final. The line then counts what was
 * turned so far and says "No new versions yet" for none: it must never say what a face-down card
 * will do (the face-down glow is only a hint, and the hint can be wrong).
 */
export function versionLine(filled: number, done = true): string {
  if (filled < 1) return done ? 'No new versions' : 'No new versions yet'
  return `+${filled.toLocaleString()} ${filled === 1 ? 'version' : 'versions'}`
}

// ---- The face-down phase (Anticipation and Reveal Feedback Spec, section 1) ----

/** The cards lie face down this long before the first one can be turned. */
export const FIRST_FLIP_HOLD_MS = 800
/** With reduced motion the floors are shorter, never gone (spec 7). */
export const FIRST_FLIP_HOLD_REDUCED_MS = 300
export const GLOW_MIN_MS = 400
export const GLOW_MAX_MS = 800
export const GLOW_REDUCED_MS = 150

/** How long the face-down cards are held before a flip is possible. */
export function firstFlipHold(reduced = false): number {
  return reduced ? FIRST_FLIP_HOLD_REDUCED_MS : FIRST_FLIP_HOLD_MS
}

/**
 * How long a clicked card shows its glow, face down, before its flip starts: 400 to 800 ms. The
 * first pack of a run uses the long half (`rand` varies it, 600 to 800), every later pack of a
 * multi-pack run the short end (400). Reduced motion: 150 ms. "Reveal all" (`quiet`) turns the
 * cards in a quick row without a glow of their own: they have lain face down for the hold already.
 */
export function glowTime(o: { packNo?: number; quiet?: boolean; reduced?: boolean }, rand: () => number = Math.random): number {
  if (o.quiet) return 0
  if (o.reduced) return GLOW_REDUCED_MS
  if ((o.packNo ?? 1) >= 2) return GLOW_MIN_MS
  const r = Math.min(1, Math.max(0, rand()))
  return Math.round((GLOW_MIN_MS + GLOW_MAX_MS) / 2 + r * (GLOW_MAX_MS - GLOW_MIN_MS) / 2)
}

/** The strongest glow step of the backs. */
export const DRY_GLOW_MAX = 4

/**
 * How strongly every face-down card glows, 0 to 4, from the run of packs without a Holo or
 * Special: one step every three packs. It is a look only: no number is shown and no odds change.
 */
export function dryGlowLevel(dryStreak: number): number {
  if (!Number.isFinite(dryStreak) || dryStreak < 0) return 0
  return Math.min(DRY_GLOW_MAX, Math.floor(dryStreak / 3))
}

/**
 * Does this face-down card get the slot glow? The server's `hint` decides, and only the hint: it is
 * wrong on purpose now and then, so `fillsSlot` (the truth) must not be shown before the flip.
 */
export function showsHint(p: Pick<PulledCard, 'hint'>): boolean {
  return p.hint === true
}

/** "Limited" under a face-down card: the print tier, the optional hint of the spec. '' when unknown. */
export function tierHint(card: Pick<PulledCard['card'], 'printTier'>): string {
  return card.printTier ? card.printTier : ''
}

/**
 * The sound of a turned card (spec 2, the escalation table): a Holo or Special has its escalated
 * sting even as a duplicate; a card that fills a slot below that has the slot-fill sound; anything
 * else, also a Foil that was already owned, the base sound. A Holo or Special that fills a slot
 * plays both: its sting, then the slot-fill sound (`then`).
 */
export function revealSounds(p: Pick<PulledCard, 'fillsSlot'>, level: number): { now: Sfx; then: Sfx | null } {
  if (level >= 2) return { now: level >= 3 ? 'reveal-3' : 'reveal-2', then: p.fillsSlot ? 'slot-fill' : null }
  return { now: p.fillsSlot ? 'slot-fill' : 'reveal-0', then: null }
}

/** Screen effects for a turned card: the treatment's level, but none for a Foil that fills nothing (a standard flip). */
export function fxLevel(p: Pick<PulledCard, 'fillsSlot' | 'isNew'>, level: number): number {
  return level === 1 && !p.fillsSlot && !p.isNew ? 0 : level
}

/** The slots this pack filled, in the order the cards were pulled: card name and treatment. */
export function filledSlots(pulls: PulledCard[]): { id: number; title: string; treatment: string }[] {
  return pulls.filter((p) => p.fillsSlot).map((p) => ({ id: p.card.id, title: p.card.title, treatment: p.card.treatment }))
}

/**
 * What a pull is, for the colour of its glowing border and of its line: a card you did not own,
 * a treatment you did not hold of a card you own, or a duplicate.
 */
export type PullStatus = 'new' | 'version' | 'duplicate'
export function pullStatus(p: PulledCard): PullStatus {
  if (p.outcome === 'new') return 'new'
  return p.outcome === 'duplicate' || firstNormal(p) ? 'duplicate' : 'version'
}

/** How strong the status glow is: 1 on a Normal, 2 Foil, 3 Holo, 4 Special. */
export function glowLevel(treatment: string): 1 | 2 | 3 | 4 {
  return (visualFor(treatment).revealLevel + 1) as 1 | 2 | 3 | 4
}

export interface CoinEffect {
  /** 0 = a small tick of the number, 1 = a few coins, 2 = a shower, 3 = a real burst. */
  level: 0 | 1 | 2 | 3
  /** Coins that fly out of the number. */
  coins: number
  /** How long the number counts up, in ms. */
  ms: number
}

/**
 * How much the value under a turned card celebrates. The steps are the price steps the economy
 * uses (20, 60, 180): a 6-coin card ticks, a 600-coin card bursts.
 */
export function coinEffect(value: number): CoinEffect {
  if (!(value > 0)) return { level: 0, coins: 0, ms: 0 }
  if (value < 20) return { level: 0, coins: 0, ms: 250 }
  if (value < 60) return { level: 1, coins: 4, ms: 450 }
  if (value < 180) return { level: 2, coins: 10, ms: 700 }
  return { level: 3, coins: Math.min(36, 18 + Math.floor(value / 60)), ms: 1000 }
}

export interface FlipTiming {
  /** The beat a card is held, face down, before it turns. */
  hold: number
  /** How long the turn takes. */
  turn: number
}

/**
 * How one card turns. Every flip has its own length (`rand` varies it a little); a Holo or a
 * Special, and any card that fills a version slot, is held for a beat and turns slower. From the
 * third pack opened in a row the commons (Normal cards that fill nothing) turn faster.
 * "Reveal all" (`quiet`) turns everything fast, and reduced motion has no hold and no variation.
 */
export function flipTiming(
  o: { level: number; fills?: boolean; packNo?: number; quiet?: boolean; reduced?: boolean }, rand: () => number = Math.random,
): FlipTiming {
  if (o.reduced) return { hold: 0, turn: 200 }
  if (o.quiet) return { hold: 0, turn: 320 }
  const r = Math.min(1, Math.max(0, rand()))
  let hold = 0
  let turn = 480 + r * 160 // Normal: 480 to 640 ms
  if (o.level === 1) turn = 560 + r * 160
  if (o.level === 2) { hold = 260 + r * 80; turn = 780 + r * 140 }
  if (o.level >= 3) { hold = 420 + r * 120; turn = 940 + r * 160 }
  if (o.fills) { hold = Math.max(hold, 240 + r * 60); turn = Math.max(turn, 720 + r * 120) }
  if ((o.packNo ?? 1) >= 3 && o.level === 0 && !o.fills) turn = 300 + r * 80
  return { hold: Math.round(hold), turn: Math.round(turn) }
}

export interface PrimaryAction { kind: 'next' | 'another' | 'buy' | 'free' | 'earn'; label: string }

/**
 * The main button under a finished pack. Opening goes first, always: the next pack of a row, a
 * sealed pack, then buying one. Only when none of that is possible does it point at a way to get
 * a pack (today's free pack, or tagging for coins). "Close" is never the main button.
 */
export function primaryAction(o: { remaining: number; sealed: number; coins: number; price: number; freeReady?: boolean }): PrimaryAction {
  if (o.remaining > 0) return { kind: 'next', label: `Next pack (${o.remaining} to go)` }
  if (o.sealed > 0) return { kind: 'another', label: `Open another · ${o.sealed} sealed` }
  if (o.coins >= o.price) return { kind: 'buy', label: `Buy and open another · 🪙 ${o.price.toLocaleString()}` }
  if (o.freeReady) return { kind: 'free', label: 'Claim your free pack' }
  return { kind: 'earn', label: 'Tag these cards to earn coins' }
}

/**
 * The near-miss line, or '' when there is nothing to say. The roll landed just short of the next
 * treatment: "Almost a Foil!". Only for Normal and Foil pulls; a Holo or Special is already the win.
 */
export function nearMissNote(p: PulledCard, nextName: string | null): string {
  if (!p.nearMiss || !nextName || visualFor(p.card.treatment).revealLevel >= 2) return ''
  return `Almost ${/^[aeiou]/i.test(nextName) ? 'an' : 'a'} ${nextName}!`
}

export interface NextStep { kind: 'fuse' | 'wanted'; id: number; title: string; treatment: string; label: string }

/**
 * What these pulls make possible right now, at most `max` steps: a card that reached enough copies
 * to merge (the count is the card's own, from its `fuseOptions`), then a card a collector wants,
 * with what the bonus is in coins (`wantedText`). One step per card, in the order the cards were pulled.
 */
export function nextSteps(
  pulls: PulledCard[], fuseCopies: number, treatmentName: (id: string) => string, nextTreatment: (id: string) => string | null, max = 3,
  wantedText: (card: PulledCard['card']) => string = () => '',
): NextStep[] {
  const seen = new Set<number>()
  const fuse: NextStep[] = []
  const wanted: NextStep[] = []
  for (const p of pulls) {
    const c = p.card
    if (seen.has(c.id)) continue
    seen.add(c.id)
    const up = c.fuseFrom ? nextTreatment(c.fuseFrom) : null
    if (c.fuseFrom && up) {
      const own = c.fuseOptions?.find((o) => o.from === c.fuseFrom)?.copies ?? c.fuseCopies ?? fuseCopies
      fuse.push({ kind: 'fuse', id: c.id, title: c.title, treatment: c.fuseFrom, label: `Merge ${own} ${treatmentName(c.fuseFrom)} → ${treatmentName(up)}: ${c.title}` })
    } else if (c.wanted) {
      const bonus = wantedText(c)
      wanted.push({ kind: 'wanted', id: c.id, title: c.title, treatment: c.treatment, label: `★ ${c.title} is wanted: ${bonus || 'it sells for more until the next price update'}` })
    }
  }
  return [...fuse, ...wanted].slice(0, max)
}

/** The badge on a flipped card: NEW for a card you did not own and for a version you did not hold (any pull that fills a slot). */
export function pullBadge(p: PulledCard): string | undefined {
  return p.isNew || p.fillsSlot || (p.outcome === 'version' && !firstNormal(p)) ? 'NEW' : undefined
}

/** "🪙 12": what the pulled copy is worth on the market. */
export function valueNote(p: PulledCard): string {
  return p.value > 0 ? `🪙 ${p.value.toLocaleString()}` : ''
}

/** What the merge button says after a failed merge: the server's reason when it gave one. */
export function fuseFailText(title: string, err: unknown): string {
  const e = err as { status?: number; message?: string }
  return e && e.status === 409 && e.message ? `${title}: ${e.message}` : 'That did not work. Try again.'
}

export function openPacks(opts: OpeningOptions): void {
  const nameOf = opts.treatmentName ?? ((id: string) => visualFor(id).label)
  let remaining = opts.count // packs still to open after the current one
  let opened = 0 // packs bought in this overlay so far
  let total = opts.count // grows when the player chooses "Open another"
  let pulls: PulledCard[] = []
  let revealed = 0
  let revealingAll = false
  let lastEnd = 0 // when the slowest turn that is under way shows its card (performance.now())
  let gained = 0 // versions counted in the pill's "+N" that is on screen
  let armedAt = 0 // the face-down hold: no card can be turned before this moment (performance.now())
  let dryLevel = 0 // how strongly the backs of the current pack glow (0 to 4)
  let shownFills: PulledCard[] = [] // the slot-filling cards turned so far, in the order they were turned
  let gainTimer: ReturnType<typeof setTimeout> | undefined
  const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
  // Milestones the current pack reached: the toast is held back until its last card is face up.
  // (No coins are held back any more: a milestone pays packs, and only when it is claimed.)
  let pending: unknown[] = []
  const held = () => 0
  const announce = () => {
    const amounts = pending
    pending = []
    toastMilestones(amounts)
  }

  const root = document.createElement('div')
  root.className = 'opening'
  root.setAttribute('role', 'dialog')
  root.setAttribute('aria-modal', 'true')
  root.setAttribute('aria-label', `Opening ${opts.pack.name}`)
  root.tabIndex = -1
  document.body.append(root)
  document.body.classList.add('no-scroll')
  enableTilt(root)

  // The page behind must not receive keys or focus (Enter on the shop's Open button bought a second pack).
  const page = document.querySelector<HTMLElement>('#app')
  const wasInert = page?.inert ?? false
  if (page) page.inert = true
  const returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null

  const timers = new Set<ReturnType<typeof setTimeout>>()
  const later = (fn: () => void, ms: number) => {
    const id = setTimeout(() => { timers.delete(id); if (!closed) fn() }, ms)
    timers.add(id)
  }

  // Escape and any route change (browser Back, nav links) close the overlay.
  let closed = false
  const close = () => {
    if (closed) return
    closed = true
    timers.forEach(clearTimeout)
    timers.clear()
    if (gainTimer !== undefined) clearTimeout(gainTimer)
    opts.settleVersions?.() // cards still face down: their versions count from now
    stopCelebrations()
    unbind()
    root.remove()
    document.body.classList.remove('no-scroll')
    if (page) page.inert = wasInert
    returnFocus?.focus({ preventScroll: true })
    announce() // closed with cards still face down: the milestone was paid all the same, so say it now
    opts.onClose()
  }
  const unbind = bindOverlay(window, close)

  /** `n`: which pack the counter names (the one about to be picked, or the one being revealed). */
  const versionPill = () => {
    const v = opts.versions?.()
    return v ? `<span class="pill opening-versions" data-el="vpill" title="Foil, Holo and Special versions you hold, of three per card"><b data-el="vfilled" data-value="${v.filled}">${v.filled.toLocaleString()}</b> / ${v.total.toLocaleString()} versions<span class="opening-vdelta" data-el="vdelta" hidden></span></span>` : ''
  }
  const hud = (n = opened) => `
    <div class="opening-hud">
      <span class="pill pill-coins" title="Coins"><span aria-hidden="true">🪙</span> <b>${(opts.coins() - held()).toLocaleString()}</b></span>
      ${versionPill()}
      ${total > 1 ? `<span class="opening-counter">Pack ${Math.min(n, total)} / ${total}</span>` : ''}
      ${opts.sealed() > 0 ? `<span class="opening-counter">${opts.sealed()} sealed left</span>` : ''}
      <button class="opening-skip" data-act="close" aria-label="Close">✕</button>
    </div>`

  const stageEl = () => root.querySelector<HTMLElement>('.opening-stage') ?? root

  function show(html: string, focus?: string) {
    // Toasts shown inside the overlay live in a layer of their own: rewriting the stage must not remove them.
    const toasts = root.querySelector(':scope > .toasts')
    root.innerHTML = html
    if (toasts) root.append(toasts)
    const target = focus ? root.querySelector<HTMLElement>(focus) : null
    ;(target ?? root).focus({ preventScroll: true })
  }

  root.addEventListener('click', (e) => {
    const t = e.target as HTMLElement
    const act = t.closest<HTMLElement>('[data-act]')?.dataset.act
    if (act === 'close') return close()
    const go = t.closest<HTMLElement>('[data-go]')
    if (go) { const to = go.dataset.go!; close(); location.hash = to; return }
    const panel = t.closest<HTMLElement>('[data-inspect]')
    if (panel) return void opts.inspect?.(Number(panel.dataset.inspect), panel.dataset.price === '1')
    const fuseBtn = t.closest<HTMLButtonElement>('[data-fuse]')
    if (fuseBtn && !fuseBtn.disabled && opts.fuse) {
      fuseBtn.disabled = true
      const title = fuseBtn.dataset.title ?? 'The card'
      opts.fuse(Number(fuseBtn.dataset.fuse), fuseBtn.dataset.treatment!).then((became) => {
        if (closed) return
        if (became === null) { fuseBtn.disabled = false; return } // "Keep them" in the confirmation
        play('reveal-1')
        fuseBtn.textContent = `Merged ✓ ${title} is now ${nameOf(became)}`
        fuseBtn.classList.add('is-done')
        syncVersions()
      }).catch((err: unknown) => {
        // The server says why ("Fusing needs 4 copies, you have 3"). A refusal stays refused, so the
        // button stays off; only a failed connection can be tried again.
        const refused = (err as { status?: number }).status === 409
        fuseBtn.disabled = refused
        fuseBtn.textContent = fuseFailText(title, err)
      })
      return
    }
    if (act === 'all') return revealAll()
    if (act === 'next') return sealed()
    if (act === 'another') { remaining += 1; total += 1; return sealed() }
    const flip = t.closest<HTMLElement>('.flip')
    if (flip) {
      const i = Number(flip.dataset.i)
      // A turned card opens its panel on top of the overlay; a face-down one turns.
      if (flip.classList.contains('flipped')) opts.inspect?.(pulls[i].card.id)
      else reveal(i, false)
    }
  })
  // Keep Tab inside the overlay.
  root.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return
    const items = [...root.querySelectorAll<HTMLElement>('button:not([disabled]), a[href]')]
    if (!items.length) return
    const first = items[0]
    const last = items[items.length - 1]
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
  })

  // ---- Stage 1: buy, then the sealed pack ----
  function message(html: string) {
    show(`${hud()}<div class="opening-stage">${html}</div>`, '[data-focus]')
  }

  function shortOfCoins() {
    const need = Math.max(0, opts.price() - opts.coins())
    message(`
      <h2 class="results-title">${need ? `${need.toLocaleString()} coins short` : 'Not enough coins'}</h2>
      <p class="opening-hint" role="alert">The next ${esc(opts.pack.name)} costs 🪙 ${opts.price().toLocaleString()} and you have 🪙 ${opts.coins().toLocaleString()}.</p>
      <div class="opening-actions">
        <a class="btn" href="#/organize" data-focus>Tag cards to earn coins</a>
        <a class="btn btn-ghost" href="#/market">Sell cards</a>
        <button class="btn btn-ghost" data-act="close">Back to the shop</button>
      </div>`)
  }

  // The sealed pack(s) are shown first. Nothing is opened yet: leaving here keeps the pack sealed.
  function sealed() {
    // Opening several in a row: one pack, one tap. A single pack: pick one of three first.
    const picks = opts.count > 1 ? 1 : 3
    picking = false
    show(`
      ${hud(opened + 1)}
      <div class="opening-stage">
        <div class="pack-picks" data-n="${picks}">
          ${Array.from({ length: picks }, (_, i) => `
            <button class="pack-hit" data-pick="${i}" style="--i:${i}" aria-label="${picks > 1 ? `Pack ${i + 1} of ${picks}: pick it and tear it open` : 'Tear the pack open'}">${packArtHTML(opts.pack, 'pack-float')}</button>`).join('')}
        </div>
        <p class="opening-hint" role="status">${picks > 1 ? 'Pick your pack' : 'Tap the pack to tear it open'}</p>
      </div>`, picks > 1 ? '.pack-hit[data-pick="1"]' : '.pack-hit')
    root.querySelectorAll<HTMLElement>('.pack-hit').forEach((b) => b.addEventListener('click', () => void pick(b)))
  }

  // ---- Stage 2: the pick opens the pack on the server, then it tears ----
  let picking = false
  async function pick(chosen: HTMLElement) {
    if (picking) return
    picking = true
    root.querySelectorAll<HTMLElement>('.pack-hit').forEach((b) => {
      if (b === chosen) b.classList.add('is-chosen')
      else { b.classList.add('is-out'); b.setAttribute('disabled', '') }
    })
    const hint = root.querySelector('.opening-hint')
    if (hint) hint.textContent = ''
    // The run of packs without a Holo or Special as it stood BEFORE this pack: the number after it
    // would say whether this pack holds one.
    dryLevel = dryGlowLevel(opts.dryStreak?.() ?? 0)
    let next: PulledCard[] | null
    try {
      next = await opts.nextPack()
    } catch (e) {
      if (closed) return
      const status = (e as { status?: number }).status
      if (status === 402) return shortOfCoins()
      if (status === 409 && opts.sealed() === 0) {
        message(`<p class="opening-hint" role="alert">You have no sealed ${esc(opts.pack.name)} left.</p>
          <button class="btn" data-act="close" data-focus>Back to the shop</button>`)
        return
      }
      const text = e instanceof Error && status ? e.message : 'Could not reach the server. Try again in a moment.'
      message(`<p class="opening-hint" role="alert">${esc(text)}</p><button class="btn" data-act="close" data-focus>Back to the shop</button>`)
      return
    }
    if (closed) return
    if (!next) return close() // another call is still pending: don't stack a second one
    pulls = next
    pending = opts.milestones?.() ?? []
    revealed = 0
    revealingAll = false
    lastEnd = 0
    shownFills = []
    opened += 1
    remaining -= 1
    const pack = chosen.querySelector<HTMLElement>('.pack')!
    play('crack')
    shake(stageEl(), 1)
    pack.classList.add('pack-tearing')
    later(faceDown, 650) // the top strip flies off, then the cards come out
  }

  /** The version line and the slots filled so far. It only ever counts cards that are face up. */
  function drawHead(done: boolean) {
    const head = root.querySelector<HTMLElement>('[data-el="head"]')
    if (!head) return
    const n = shownFills.length
    head.innerHTML = `
      <p class="opening-versionline${n ? ' is-gain' : ''}${done ? '' : ' is-running'}">${esc(versionLine(n, done))}</p>
      ${n ? `<ul class="opening-filled" aria-label="Version slots this pack filled">${shownFills.map((f) =>
        `<li style="--rarity:${visualFor(f.card.treatment).color}"><b>${esc(f.treatmentName || nameOf(f.card.treatment))}</b> ${esc(f.card.title)}</li>`).join('')}</ul>` : ''}`
  }

  // ---- Stage 3: face-down cards. The slot glow is the server's hint (it can be wrong on purpose:
  // never `fillsSlot`); every back glows with the dry streak; under a back its print tier.
  // Nothing can be turned for the first 800 ms (300 with reduced motion). ----
  function faceDown() {
    const hold = firstFlipHold(reduced())
    armedAt = performance.now() + hold
    show(`
      ${hud()}
      <div class="opening-stage opening-stage-cards">
        <div class="opening-head" data-el="head" role="status"></div>
        <div class="reveal-row is-held" data-dry="${dryLevel}" style="--cols:${revealCols(pulls.length)}; --rows:${Math.ceil(pulls.length / revealCols(pulls.length))}; --dry:${dryLevel}">
          ${pulls.map((p, i) => `
            <div class="flip-slot">
              <button class="flip${showsHint(p) ? ' has-hint' : ''}" data-i="${i}" style="--delay:${i * 70}ms; --rarity:${visualFor(p.card.treatment).color}; --glow:${glowLevel(p.card.treatment)}" aria-label="Reveal card ${i + 1} of ${pulls.length}${showsHint(p) ? '. It glows: it may fill a version slot' : ''}">
                <div class="flip-inner">
                  <div class="flip-back"><span>✦</span></div>
                  <div class="flip-front" inert>${cardHTML(p.card, { size: 'md', badge: pullBadge(p) })}</div>
                </div>
              </button>
              <p class="flip-value" data-worth="${i}"></p>
              <p class="flip-note is-tier" data-note="${i}" aria-live="polite">${esc(tierHint(p.card))}</p>
              <p class="flip-near" data-near="${i}"></p>
            </div>`).join('')}
        </div>
        <div class="opening-foot">
          <p class="opening-summary" data-el="summary" role="status"></p>
          <div class="opening-actions" data-el="actions">
            <button class="btn btn-ghost" data-act="all" disabled>Reveal all</button>
          </div>
        </div>
      </div>`, '.flip')
    drawHead(false)
    later(() => {
      root.querySelector('.reveal-row')?.classList.remove('is-held')
      const all = root.querySelector<HTMLButtonElement>('[data-act="all"]')
      if (all && !revealingAll) all.disabled = false
    }, hold)
  }

  /** Screen effects for a rare card: sparks for a Foil, a rainbow burst for a Holo, the full gold show for a Special. */
  function fx(el: HTMLElement, level: number, treatment: string) {
    if (level < 1) return
    const r = el.getBoundingClientRect()
    celebrate({
      level: Math.min(3, level) as RarityLevel,
      hue: opts.pack.hue,
      origin: { x: r.left + r.width / 2, y: r.top + r.height / 2 },
      host: root,
      label: level >= 2 ? `${nameOf(treatment).toUpperCase()}!` : undefined,
      intensity: opts.fxIntensity ?? 1,
    })
  }

  /** The version pill in the overlay's bar: the number as the store has it now. */
  function syncVersions() {
    const v = opts.versions?.()
    const el = root.querySelector<HTMLElement>('[data-el="vfilled"]')
    if (v && el) countTo(el, v.filled, 300)
  }

  /**
   * The slot-fill moment of one card, on the frame its result shows: the pill goes up, the version
   * line counts it, a lock-in ring and a floating "+1 version". Its sound is played by showResult.
   */
  function versionFilled(el: HTMLElement, p: PulledCard) {
    opts.revealVersion?.(p.card.id, p.card.treatment)
    shownFills.push(p)
    drawHead(false)
    el.classList.add('is-lockin')
    const r = el.getBoundingClientRect()
    floatText('+1 version', { x: r.left + r.width / 2, y: r.top + r.height * 0.42 }, 'fx-version', root) // over the card's middle, on its own dark plate
    syncVersions()
    const pill = root.querySelector<HTMLElement>('[data-el="vpill"]')
    const delta = root.querySelector<HTMLElement>('[data-el="vdelta"]')
    if (!pill || !delta) return
    gained += 1
    delta.textContent = `+${gained}`
    delta.hidden = false
    pill.classList.remove('is-gain')
    void pill.offsetWidth // restart the pop
    pill.classList.add('is-gain')
    if (gainTimer !== undefined) clearTimeout(gainTimer)
    gainTimer = setTimeout(() => {
      gained = 0
      root.querySelector<HTMLElement>('[data-el="vdelta"]')?.setAttribute('hidden', '')
      root.querySelector<HTMLElement>('[data-el="vpill"]')?.classList.remove('is-gain')
    }, 2000)
  }

  /** The card is face up: its status glow, its value (counting up, with coins), its line, and the slot-fill moment. */
  function showResult(i: number, el: HTMLElement, quiet: boolean, star: boolean) {
    const p = pulls[i]
    const level = visualFor(p.card.treatment).revealLevel
    const sounds = revealSounds(p, level)
    play(sounds.now)
    if (sounds.then) { const then = sounds.then; later(() => play(then), 240) }
    if (!quiet || star) fx(el, fxLevel(p, level), p.card.treatment)
    const status = pullStatus(p)
    el.classList.add(`st-${status}`)
    const note = root.querySelector<HTMLElement>(`[data-note="${i}"]`)
    if (note) {
      note.classList.remove('is-tier')
      note.textContent = pullNote(p, nameOf)
      note.dataset.status = status
    }
    // The slot-filling card: the pill, the version line and "+1 version" on this same frame.
    if (p.fillsSlot) versionFilled(el, p)
    const worth = root.querySelector<HTMLElement>(`[data-worth="${i}"]`)
    if (worth && p.value > 0) {
      const effect = coinEffect(p.value)
      worth.innerHTML = '<span aria-hidden="true">🪙</span> <b data-value="0">0</b>'
      worth.dataset.level = String(effect.level)
      worth.setAttribute('aria-label', `Worth ${p.value.toLocaleString()} coins`)
      countTo(worth.querySelector('b')!, p.value, quiet ? Math.min(effect.ms, 300) : effect.ms)
      worth.classList.add('is-in')
      if (!quiet || star) {
        const r = worth.getBoundingClientRect()
        coinBurst({ x: r.left + r.width / 2, y: r.top + r.height / 2 }, effect.coins, effect.level, root)
        if (effect.level >= 2) later(() => play('coin'), 320)
      }
    }
    const nearEl = root.querySelector<HTMLElement>(`[data-near="${i}"]`)
    const next = opts.nextTreatment?.(p.card.treatment) ?? null
    const near = nearMissNote(p, next ? nameOf(next) : null)
    if (nearEl && near) {
      nearEl.textContent = near
      later(() => play('near-miss'), 250)
    }
  }

  /** `quiet` = part of "Reveal all": no effects, unless this is the best card of the pack (`star`). */
  function reveal(i: number, quiet: boolean, star = false) {
    const el = root.querySelector<HTMLElement>(`.flip[data-i="${i}"]`)
    if (!el || el.classList.contains('flipped') || el.classList.contains('is-holding') || el.classList.contains('is-glowing')) return
    if (performance.now() < armedAt) return // the face-down hold: nothing turns yet
    const p = pulls[i]
    const level = visualFor(p.card.treatment).revealLevel
    const t = flipTiming({ level, fills: p.fillsSlot, packNo: opened, quiet, reduced: reduced() })
    // The glow before the flip: the card is picked and shows its edge, face down, then turns.
    const glow = glowTime({ packNo: opened, quiet, reduced: reduced() })
    el.style.setProperty('--turn', `${t.turn}ms`)
    const turn = () => {
      el.classList.remove('is-holding')
      el.classList.add('flipped')
      el.setAttribute('aria-label', `${p.card.title}, ${nameOf(p.card.treatment)}. Open the card panel`)
      el.querySelector('.flip-front')?.removeAttribute('inert')
      play('flip')
      later(() => showResult(i, el, quiet, star), Math.round(t.turn * 0.4))
    }
    // The beat before a rare card or a new version turns: the card lifts and waits.
    const start = () => {
      el.classList.remove('is-glowing')
      if (t.hold > 0) { el.classList.add('is-holding'); later(turn, t.hold) } else turn()
    }
    if (glow > 0) { el.classList.add('is-glowing'); later(start, glow) } else start()
    const shown = performance.now() + glow + t.hold + t.turn * 0.4
    lastEnd = Math.max(lastEnd, shown)
    revealed++
    if (revealed === pulls.length) later(finished, lastEnd - performance.now() + (quiet ? 300 : 650))
  }

  function revealAll() {
    if (revealingAll) return
    revealingAll = true
    const btn = root.querySelector<HTMLButtonElement>('[data-act="all"]')
    if (btn) btn.disabled = true
    if (performance.now() < armedAt) return // the face-down hold (the button is disabled until then)
    const open = pulls.map((_, i) => i).filter((i) => !root.querySelector(`.flip[data-i="${i}"]`)?.matches('.flipped, .is-holding, .is-glowing'))
    if (!open.length) return
    // One celebration, for the best card that is still face down (the most valuable one on a tie).
    const lvl = (i: number) => visualFor(pulls[i].card.treatment).revealLevel
    const best = open.reduce<number | null>((b, i) =>
      b === null || lvl(i) > lvl(b) || (lvl(i) === lvl(b) && pulls[i].value > pulls[b].value) ? i : b, null)
    open.forEach((i, step) => later(() => reveal(i, true, i === best), step * 110))
  }

  // ---- Every card is face up: the cards stay. The version line and the filled slots lead, above
  // the row; the summary and the buttons under it change. ----
  function finished() {
    const summary = root.querySelector<HTMLElement>('[data-el="summary"]')
    const actions = root.querySelector<HTMLElement>('[data-el="actions"]')
    if (!summary || !actions) return
    opts.settleVersions?.() // nothing is face down any more
    // Every card is up: the line is final now ("No new versions" when none filled a slot), with the slots in pull order.
    shownFills = pulls.filter((p) => p.fillsSlot)
    drawHead(true)
    summary.textContent = summaryText(summarize(pulls))
    announce() // every card is face up: now the milestone toast, and the balance below includes it
    const hudEl = root.querySelector('.opening-hud')
    if (hudEl) hudEl.outerHTML = hud()
    const main = primaryAction({ remaining, sealed: opts.sealed(), coins: opts.coins(), price: opts.price(), freeReady: opts.freePack?.() === 'ready' })
    const primary = main.kind === 'next' ? `<button class="btn" data-act="next" data-focus>${esc(main.label)}</button>`
      : main.kind === 'another' || main.kind === 'buy' ? `<button class="btn" data-act="another" data-focus>${esc(main.label)}</button>`
        : main.kind === 'free' ? `<button class="btn" data-go="#/packs" data-focus>${esc(main.label)}</button>`
          : `<a class="btn" href="#/organize" data-focus>${esc(main.label)}</a>`
    // What these cards make possible right now: one tap, no detour through the Collection.
    const steps = nextSteps(pulls, opts.fuseCopies ?? 3, nameOf, opts.nextTreatment ?? (() => null), 3, opts.wantedText)
    const prompts = steps.map((s) => s.kind === 'fuse' && opts.fuse
      ? `<button class="opening-step" type="button" data-fuse="${s.id}" data-treatment="${esc(s.treatment)}" data-title="${esc(s.title)}">⚗ ${esc(s.label)}</button>`
      : s.kind === 'wanted' ? `<button class="opening-step" type="button" data-inspect="${s.id}" data-price="1">${esc(s.label)}</button>` : '').join('')
    root.querySelector('.opening-steps')?.remove()
    if (prompts) actions.insertAdjacentHTML('beforebegin', `<div class="opening-steps" role="group" aria-label="What you can do with these cards">${prompts}</div>`)
    actions.innerHTML = `${primary}<button class="btn btn-ghost" data-act="close">Close</button>`
    actions.querySelector<HTMLElement>('[data-focus]')?.focus({ preventScroll: true })
  }

  sealed()
}
