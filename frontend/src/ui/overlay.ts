/**
 * One rule for full-screen overlays (the pack opening): while one is open, page keyboard handlers
 * stand down (they check overlayOpen()), the overlay's own Escape is captured so pages never see
 * it, and any route change closes the overlay.
 */

export const OVERLAY_SELECTOR = '.opening'

/** True while a full-screen overlay is in the document. Page key handlers return early on it. */
export function overlayOpen(doc: Pick<Document, 'querySelector'> = document): boolean {
  return doc.querySelector(OVERLAY_SELECTOR) !== null
}

/**
 * Wire an overlay's lifetime to the window: Escape (capture phase, propagation stopped) and
 * hashchange (browser Back, nav links) both call close. Returns a function removing both listeners.
 */
export function bindOverlay(win: EventTarget, close: () => void): () => void {
  const onKey = (e: Event) => {
    if ((e as KeyboardEvent).key !== 'Escape') return
    // A modal dialog on top of the overlay (the full-screen viewer, the card panel, a confirmation)
    // takes Escape for itself.
    if (typeof document !== 'undefined' && document.querySelector('dialog[open]')) return
    e.stopPropagation()
    e.preventDefault()
    close()
  }
  const onHash = () => close()
  win.addEventListener('keydown', onKey, { capture: true })
  win.addEventListener('hashchange', onHash)
  return () => {
    win.removeEventListener('keydown', onKey, { capture: true })
    win.removeEventListener('hashchange', onHash)
  }
}
