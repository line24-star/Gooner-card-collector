/**
 * How each treatment LOOKS and SOUNDS. Economy numbers (multiplier, odds) come from the
 * server (backend/app/game/treatments.py). Adding a treatment = one entry here + CSS class.
 */

import type { TreatmentId } from '../data/types.ts'

export interface TreatmentVisual {
  className: string // applied to .card, styled in cards.css
  color: string // CSS var for rarity color (glows, badges, borders)
  label: string
  /** Pack-reveal escalation: 0 = quiet flip … 3 = flash + shake + fanfare. */
  revealLevel: 0 | 1 | 2 | 3
  sparkles: boolean
  /** Full art: the media fills the whole card face. Only the rarest treatments get it. */
  fullArt: boolean
}

export const TREATMENT_VISUALS: Record<string, TreatmentVisual> = {
  normal: { className: 't-normal', color: 'var(--r-normal)', label: 'Normal', revealLevel: 0, sparkles: false, fullArt: false },
  foil: { className: 't-foil', color: 'var(--r-foil)', label: 'Foil', revealLevel: 1, sparkles: false, fullArt: false },
  holo: { className: 't-holo', color: 'var(--r-holo)', label: 'Holo', revealLevel: 2, sparkles: false, fullArt: true },
  special: { className: 't-special', color: 'var(--r-special)', label: 'Special', revealLevel: 3, sparkles: true, fullArt: true },
}

export function visualFor(id: TreatmentId): TreatmentVisual {
  return TREATMENT_VISUALS[id] ?? TREATMENT_VISUALS.normal
}
