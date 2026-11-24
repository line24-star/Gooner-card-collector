/**
 * The one in-app confirmation dialog. It replaces the browser's confirm() everywhere.
 *
 * Only actions that lose something ask: selling a last copy, a merge that empties a version slot,
 * selling all duplicates, taking a collector's offer on an only copy, stopping the server.
 * Everything else acts at once and says what happened in a line or a toast.
 *
 * It is a modal <dialog>: it sits above the card panel, the Messages panel and the pack overlay,
 * the browser keeps the focus inside it, and Esc cancels. Enter confirms (unless the focus is on
 * Cancel). The focus goes back to where it was. The gain and the loss are shown as two boxes of
 * the same size and weight.
 */

import './confirm.css'
import { esc } from '../cards/card.ts'
import { visualFor } from '../cards/treatments.ts'
import type { Card, FuseOption } from '../data/types.ts'

export interface ConfirmRow {
  label: string
  value: string
  /** gain: coins or a copy received. loss: what goes away, in the treatment's colour (never red). */
  tone?: 'gain' | 'loss' | 'plain'
  /** CSS colour for a loss row: the treatment whose slot empties. */
  color?: string
}

export interface ConfirmOptions {
  title: string
  text?: string
  rows?: ConfirmRow[]
  note?: string
  /** The label of the button that goes ahead, e.g. "Sell · 🪙 31". */
  confirm: string
  cancel?: string
}

export function confirmHTML(o: ConfirmOptions): string {
  const rows = (o.rows ?? []).map((r) =>
    `<div class="confirm-row is-${r.tone ?? 'plain'}"${r.color ? ` style="--tone:${r.color}"` : ''}>
      <span class="confirm-label">${esc(r.label)}</span><b class="confirm-value">${esc(r.value)}</b></div>`).join('')
  return `
    <h2 class="confirm-title" id="confirm-title">${esc(o.title)}</h2>
    ${o.text ? `<p class="confirm-text">${esc(o.text)}</p>` : ''}
    ${rows ? `<div class="confirm-rows">${rows}</div>` : ''}
    ${o.note ? `<p class="confirm-note muted">${esc(o.note)}</p>` : ''}
    <div class="confirm-acts">
      <button type="button" class="btn btn-ghost" data-confirm="no">${esc(o.cancel ?? 'Cancel')}</button>
      <button type="button" class="btn" data-confirm="yes">${esc(o.confirm)}</button>
    </div>`
}

/** Ask. Resolves true when the player goes ahead, false on Cancel, Esc or a click beside the dialog. */
export function confirmDialog(o: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    const back = document.activeElement as HTMLElement | null
    const d = document.createElement('dialog')
    d.className = 'confirm'
    d.setAttribute('aria-labelledby', 'confirm-title')
    d.innerHTML = confirmHTML(o)
    let answer = false
    const done = (yes: boolean) => { answer = yes; if (d.open) d.close(); else finish() }
    let finished = false
    const finish = () => {
      if (finished) return
      finished = true
      d.remove()
      if (back?.isConnected) back.focus({ preventScroll: true })
      resolve(answer)
    }
    d.addEventListener('click', (e) => {
      const t = e.target as HTMLElement
      if (t === d) return done(false) // the backdrop
      const b = t.closest<HTMLElement>('[data-confirm]')
      if (b) done(b.dataset.confirm === 'yes')
    })
    d.addEventListener('keydown', (e) => {
      e.stopPropagation() // page and panel shortcuts stand down while the question is open
      if (e.key !== 'Enter') return
      if ((e.target as HTMLElement).closest('[data-confirm="no"]')) return // Enter on Cancel cancels
      e.preventDefault()
      done(true)
    })
    d.addEventListener('close', finish) // Esc ends here too, with answer still false
    document.body.append(d)
    d.showModal()
    d.querySelector<HTMLElement>('[data-confirm="yes"]')?.focus()
  })
}

// ---- What each loss-making action asks (pure, so the texts are tested) ----

const coin = (n: number) => `🪙 ${Math.round(n).toLocaleString()}`
const label = (t: string) => visualFor(t).label
const isVersion = (t: string) => t === 'foil' || t === 'holo' || t === 'special'

/** "This will empty the Foil slot (−1 version)". */
export function emptiesText(treatment: string): string {
  return `This will empty the ${label(treatment)} slot (−1 version)`
}

/** "+1 version", "−1 version", "+2 versions", or "No change" for 0. */
export function netVersionsText(net: number): string {
  if (!net) return 'No change (0 versions)'
  return `${net > 0 ? '+' : '−'}${Math.abs(net)} ${Math.abs(net) === 1 ? 'version' : 'versions'}`
}

/** What selling `count` copies of one treatment pays in all, from the server's quotes. */
export function saleCoins(card: Pick<Card, 'saleQuotes' | 'sellPrices'>, treatment: string, count: number): number {
  return card.saleQuotes[treatment]?.[count - 1] ?? (card.sellPrices[treatment] ?? 0) * count
}

/**
 * The question before a sale, or null when the sale loses nothing and goes ahead at once.
 * It asks when the sale empties a version slot (the last copies of a Foil, Holo or Special) or
 * when the card leaves the collection (its last copy of all).
 */
export function saleDialog(
  card: Pick<Card, 'title' | 'copies' | 'copiesByTreatment' | 'saleQuotes' | 'sellPrices'>, treatment: string, count: number,
): ConfirmOptions | null {
  const held = card.copiesByTreatment[treatment] ?? 0
  const empties = isVersion(treatment) && count >= held
  const leaves = count >= card.copies
  if (!empties && !leaves) return null
  const coins = saleCoins(card, treatment, count)
  const loss = [empties ? emptiesText(treatment) : '', leaves ? 'The card leaves your collection until you pull it again' : ''].filter(Boolean).join('. ')
  return {
    title: `Sell your last ${label(treatment)} ${count === 1 ? 'copy' : `${count} copies`} of ${card.title}?`,
    rows: [
      { label: 'You receive', value: coin(coins), tone: 'gain' },
      { label: 'You lose', value: loss, tone: 'loss', color: visualFor(treatment).color },
    ],
    confirm: `Sell · ${coin(coins)}`,
    cancel: 'Keep it',
  }
}

/** The question before a merge that empties a version slot: what goes, what comes, the real net change. */
export function fuseDialog(card: Pick<Card, 'title'>, o: FuseOption): ConfirmOptions {
  return {
    title: `Merge ${o.copies} ${label(o.from)} copies of ${card.title}?`,
    rows: [
      { label: 'You give up', value: `${o.copies} ${label(o.from)} copies, worth ${coin(o.value)} on the market`, tone: 'loss', color: visualFor(o.from).color },
      { label: 'You get', value: `1 ${label(o.to)} copy`, tone: 'gain' },
      { label: 'Versions', value: netVersionsText(o.net_versions), tone: o.net_versions < 0 ? 'loss' : 'plain', color: visualFor(o.from).color },
      ...(o.empties ? [{ label: 'You lose', value: emptiesText(o.from).replace(' (−1 version)', ''), tone: 'loss' as const, color: visualFor(o.from).color }] : []),
    ],
    note: o.fills ? `The ${label(o.to)} copy fills its empty slot.` : `You already hold a ${label(o.to)}: the new copy fills no slot.`,
    confirm: `Merge into ${label(o.to)}`,
    cancel: 'Keep them',
  }
}

/** The question before "Sell all duplicates": the coins, and that no version slot empties. */
export function duplicatesDialog(copies: number, value: number): ConfirmOptions {
  return {
    title: `Sell ${copies.toLocaleString()} duplicate ${copies === 1 ? 'copy' : 'copies'}?`,
    rows: [
      { label: 'You receive', value: coin(value), tone: 'gain' },
      { label: 'You keep', value: 'One copy of every card and treatment: no version slot empties', tone: 'plain' },
    ],
    note: 'Sold copies cannot be merged later.',
    confirm: `Sell · ${coin(value)}`,
    cancel: 'Keep them',
  }
}

/** The question before taking a collector's offer on an only copy. */
export function offerDialog(o: { title: string; treatment: string; price: number; emptiesSlot: boolean; lastCopy: boolean }): ConfirmOptions {
  const loss = [o.emptiesSlot ? `Accepting empties the ${label(o.treatment)} slot (−1 version)` : '', o.lastCopy ? 'The card leaves your collection until you pull it again' : ''].filter(Boolean).join('. ')
  return {
    title: `Take the offer for your ${label(o.treatment)} ${o.title}?`,
    rows: [
      { label: 'You receive', value: coin(o.price), tone: 'gain' },
      { label: 'You lose', value: loss, tone: 'loss', color: visualFor(o.treatment).color },
    ],
    confirm: `Take it · ${coin(o.price)}`,
    cancel: 'Keep the card',
  }
}
