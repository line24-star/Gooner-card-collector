/**
 * Card rendering. Two layouts, chosen by the treatment (cards/treatments.ts `fullArt`):
 *   - classic (Normal, Foil): a frame with the title on top, the media in a window, tags + rarity below;
 *   - full art (Holo, Special): the media fills the whole face and title and footer are laid over it.
 *
 *   cardHTML(card)          – markup string (cheap; used by grids)
 *   enableTilt(root)        – pointer-driven 3D tilt + holo glare for any .card inside root
 */

import type { Card } from '../data/types.ts'
import { visualFor } from './treatments.ts'

export function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`)
}

/** The treatments in the order copies are listed, and the three that are versions (the ring's segments). */
const COPY_ORDER = ['normal', 'foil', 'holo', 'special']
export const RING_TREATMENTS = ['foil', 'holo', 'special'] as const

/**
 * "2 Normal · 1 Foil": the copies held per treatment, lowest first. '' when none is held.
 * `compact`: with three or more treatments, or a line too long for a small card, the names shrink to
 * their first letter ("2 N · 1 F · 1 H", "7 N · 1 S"); the tooltip keeps the long form.
 */
export function copiesLabel(copies: Record<string, number>, compact = false): string {
  const ids = [...COPY_ORDER, ...Object.keys(copies).filter((t) => !COPY_ORDER.includes(t))].filter((t) => (copies[t] ?? 0) > 0)
  const long = ids.map((t) => `${copies[t]} ${visualFor(t).label}`).join(' · ')
  // Under a small card about 18 characters fit: "14 Normal · 1 Holo" does, "7 Normal · 1 Special" does not.
  const short = compact && (ids.length > 2 || long.length > 18)
  return short ? ids.map((t) => `${copies[t]} ${visualFor(t).label.charAt(0)}`).join(' · ') : long
}

/** "Foil held · Holo missing · Special missing": what the ring shows, in words (its tooltip). */
export function ringTitle(copies: Record<string, number>): string {
  return RING_TREATMENTS.map((t) => `${visualFor(t).label} ${(copies[t] ?? 0) > 0 ? 'held' : 'missing'}`).join(' · ')
}

// Three arcs of a circle (centre 12 12, radius 8.5), clockwise from the top: Foil, Holo, Special.
const RING_ARCS = [0, 1, 2].map((i) => {
  const at = (deg: number) => {
    const a = (deg * Math.PI) / 180
    return `${(12 + 8.5 * Math.cos(a)).toFixed(2)} ${(12 + 8.5 * Math.sin(a)).toFixed(2)}`
  }
  const from = -90 + i * 120 + 13
  return `M${at(from)}A8.5 8.5 0 0 1 ${at(from + 94)}`
})

/**
 * The version ring: three segments for Foil, Holo and Special, in that order. A held version is a
 * thick arc in the treatment's colour, a missing one a thin outline. It sits on every card face.
 */
export function versionRingHTML(copies: Record<string, number>): string {
  return `<span class="vring" title="${ringTitle(copies)}"><svg viewBox="0 0 24 24" aria-hidden="true">${RING_TREATMENTS.map((t, i) =>
    `<path d="${RING_ARCS[i]}"${(copies[t] ?? 0) > 0 ? ` class="is-on" style="--seg:${visualFor(t).color}"` : ''}/>`).join('')}</svg></span>`
}

export interface CardHTMLOptions {
  size?: 'sm' | 'md' | 'lg' | 'xl'
  /** Under the card: the copies held per treatment ("2 Normal · 1 Foil"). */
  showCount?: boolean
  /** The version ring (on by default). */
  ring?: boolean
  badge?: string // e.g. "NEW"
  /** A video card plays with its sound (the full-screen viewer). Large video cards always play; small ones show a still. */
  sound?: boolean
}

/**
 * Card art: a neutral placeholder underneath; the img/video covers it and hides itself if it fails.
 * The art is part of the card: it can't be dragged out, and a video has no player controls. A large
 * video card (lg, xl) plays in a loop inside its frame; small ones show a still.
 */
function artHTML(card: Card, full = false, sound = false): string {
  // lg and xl show the original so large art stays sharp (and a GIF moves); grids use the thumbnail.
  const thumb = full && card.kind === 'image' && card.fileUrl ? card.fileUrl : card.thumbUrl ?? (card.imageUrl || null)
  const hide = `onerror="this.style.display='none'"`
  let media = ''
  if (card.kind === 'video' && card.fileUrl) {
    media = full
      ? `<video autoplay loop ${sound ? '' : 'muted '}playsinline disablepictureinpicture src="${esc(card.fileUrl)}" ${hide}></video>`
      : `<video muted playsinline preload="metadata" src="${esc(card.fileUrl)}#t=1" ${hide}></video>`
  } else if (thumb) {
    // Grid thumbnails load lazily; a large card is the thing being looked at, so it loads at once.
    media = `<img src="${esc(thumb)}" alt="" draggable="false" ${full ? '' : 'loading="lazy" '}decoding="async" ${hide}>`
  }
  const glyph = card.kind === 'video' ? '▶' : ''
  return `<div class="card-art"><div class="card-art-ph" aria-hidden="true">${glyph}</div>${media}</div>`
}

export function cardHTML(card: Card, opts: CardHTMLOptions = {}): string {
  const v = visualFor(card.treatment)
  const size = opts.size ?? 'md'
  return `
  <div class="card ${v.fullArt ? 'card-fullart ' : ''}${v.className} card-${size}" data-card-id="${card.id}" style="--rarity:${v.color}" tabindex="0"
       aria-label="${esc(card.title)}, ${v.label}">
    <div class="card-inner">
      <div class="card-head">
        <span class="card-title">${esc(card.title)}</span>
        ${card.kind === 'video' ? '<span class="card-kind" title="Video">▶</span>' : ''}
      </div>
      ${artHTML(card, size === 'lg' || size === 'xl', opts.sound)}
      <div class="card-foot">
        <span class="card-tags">${card.tags.length ? card.tags.map(esc).join(' · ') : '<i>untagged</i>'}</span>
        <span class="card-rarity">${v.label}</span>
      </div>
      <div class="card-shine"></div>
      <div class="card-glare"></div>
      ${v.sparkles ? '<div class="card-sparkles"></div>' : ''}
      ${opts.ring === false ? '' : versionRingHTML(card.copiesByTreatment)}
    </div>
    ${opts.badge ? `<span class="card-badge">${esc(opts.badge)}</span>` : ''}
    ${opts.showCount && card.copies > 0 ? `<span class="card-count" title="Copies you hold: ${esc(copiesLabel(card.copiesByTreatment))}">${esc(copiesLabel(card.copiesByTreatment, true) || `×${card.copies}`)}</span>` : ''}
  </div>`
}

/** The most a card leans, in degrees, by its width: a big card leans less, or it distorts. */
export function maxTilt(width: number): number {
  return width <= 200 ? 14 : width <= 420 ? 9 : 6
}

/** Lean and glare for a pointer at (x, y) inside a card's box, both 0..1 and clamped. */
export function tiltFor(x: number, y: number, width: number): { rx: number; ry: number; mx: number; my: number } {
  const cx = Math.min(1, Math.max(0, x))
  const cy = Math.min(1, Math.max(0, y))
  const max = maxTilt(width)
  return { rx: (0.5 - cy) * max, ry: (cx - 0.5) * max * 1.25, mx: cx * 100, my: cy * 100 }
}

/**
 * Pointer-driven 3D tilt + glare for every .card inside root (delegated). Respects reduced motion.
 *
 * One card leans at a time: the one under the pointer. It is found from the pointer position and
 * the cards' own boxes (a .card never transforms; only its .card-inner does), not from the event
 * target, because the leaning face moves under the pointer and inside a flipped 3D slot the event
 * can land on the wrong face. The card resets when the pointer leaves it or the root, on
 * pointercancel (a touch scroll), and when the window loses focus.
 */
export function enableTilt(root: HTMLElement): void {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return
  let current: HTMLElement | null = null
  let frame = 0
  let px = 0
  let py = 0

  const reset = () => {
    if (!current) return
    current.classList.remove('is-tilting')
    current.style.removeProperty('--rx')
    current.style.removeProperty('--ry')
    current = null
  }

  const cardAt = (x: number, y: number, target: EventTarget | null): HTMLElement | null => {
    const hit = (target as HTMLElement | null)?.closest?.<HTMLElement>('.card') ?? null
    const inside = (c: HTMLElement) => {
      const r = c.getBoundingClientRect()
      return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom
    }
    if (current && current.isConnected && inside(current)) return current // keep leaning the same card
    return hit && root.contains(hit) && inside(hit) ? hit : null
  }

  const apply = () => {
    frame = 0
    if (!current || !current.isConnected) return reset()
    const r = current.getBoundingClientRect()
    const t = tiltFor((px - r.left) / r.width, (py - r.top) / r.height, r.width)
    current.style.setProperty('--mx', `${t.mx}%`)
    current.style.setProperty('--my', `${t.my}%`)
    current.style.setProperty('--rx', `${t.rx}deg`)
    current.style.setProperty('--ry', `${t.ry}deg`)
    current.classList.add('is-tilting')
  }

  root.addEventListener('pointermove', (e) => {
    if (e.pointerType === 'touch') return
    px = e.clientX
    py = e.clientY
    const card = cardAt(px, py, e.target)
    if (card !== current) {
      reset()
      current = card
    }
    if (current && !frame) frame = requestAnimationFrame(apply)
  })
  root.addEventListener('pointerleave', reset)
  root.addEventListener('pointercancel', reset)
  root.addEventListener('dragstart', (e) => { if ((e.target as HTMLElement).closest('.card')) e.preventDefault() })
  window.addEventListener('blur', reset)
}
