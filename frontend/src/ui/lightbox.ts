/**
 * Full-screen viewer for one card. It shows the CARD, as large as the screen allows, with its
 * frame, its treatment and its tilt. The picture is never taken out of the card: there is no
 * separate image or video player. A video card plays in a loop inside its frame, with sound.
 *
 * It is a modal <dialog>, so it stacks above the card panel and the pack overlay, and the browser
 * handles Esc and focus for it. A click anywhere outside the card closes it; the card itself is
 * not a button.
 */

import './lightbox.css'
import { cardHTML, enableTilt, esc } from '../cards/card.ts'
import type { Card } from '../data/types.ts'

export const LIGHTBOX_SELECTOR = 'dialog.lightbox[open]'

/** True while a full-screen viewer is open. Overlays under it leave Escape to it. */
export function lightboxOpen(doc: Pick<Document, 'querySelector'> = document): boolean {
  return doc.querySelector(LIGHTBOX_SELECTOR) !== null
}

/** `mark`: a line over the card, e.g. "Not owned" for the preview of a version the player does not hold. */
export function lightboxHTML(card: Card, mark?: string): string {
  return `
    <button type="button" class="lightbox-close" data-act="close" aria-label="Close">✕</button>
    ${mark ? `<p class="lightbox-mark">${esc(mark)}</p>` : ''}
    <div class="lightbox-body${mark ? ' is-preview' : ''}">${cardHTML(card, { size: 'xl', sound: true, ring: !mark })}</div>
    ${card.kind === 'video' ? '<button type="button" class="lightbox-sound" data-act="sound" aria-label="Sound on or off">🔊</button>' : ''}
    <p class="lightbox-hint">Esc or a click beside the card closes</p>`
}

export function openLightbox(card: Card, mark?: string): HTMLDialogElement {
  document.querySelectorAll('dialog.lightbox').forEach((old) => old.remove()) // never two viewers
  const d = document.createElement('dialog')
  d.className = 'lightbox'
  d.setAttribute('aria-label', card.title)
  d.innerHTML = lightboxHTML(card, mark)
  const video = () => d.querySelector<HTMLVideoElement>('.card video')

  d.addEventListener('click', (e) => {
    const t = e.target as HTMLElement
    if (t.closest('[data-act="sound"]')) {
      const v = video()
      if (v) {
        v.muted = !v.muted
        t.closest('button')!.textContent = v.muted ? '🔇' : '🔊'
      }
      return
    }
    if (t.closest('.card')) return // the card is a card, not a button
    shut()
  })
  // Closing removes the viewer itself, without waiting for the dialog's own close event: a closed
  // viewer left in the page would still count as "a viewer is open" for the overlay under it.
  const shut = () => {
    video()?.pause()
    if (d.open) d.close()
    d.remove()
  }
  d.addEventListener('close', shut) // Esc
  enableTilt(d)
  document.body.append(d)
  d.showModal()
  // A browser may refuse to start a video with sound. Then it plays muted, and the button shows it.
  const v = video()
  if (v) {
    void v.play().catch(() => {
      v.muted = true
      const b = d.querySelector('[data-act="sound"]')
      if (b) b.textContent = '🔇'
      void v.play().catch(() => {})
    })
  }
  return d
}
