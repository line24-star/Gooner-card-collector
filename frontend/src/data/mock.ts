/** Fallbacks shown until GET /api/economy/config answers. They mirror config/economy.toml. */

import type { PackType, TreatmentInfo } from './types.ts'

export const FALLBACK_TREATMENTS: TreatmentInfo[] = [
  { id: 'normal', name: 'Normal', tier: 0, multiplier: 1, pack_weight: 780, pack_odds: 0.78, completeness_weight: 1 },
  { id: 'foil', name: 'Foil', tier: 1, multiplier: 1.5, pack_weight: 160, pack_odds: 0.16, completeness_weight: 1.5 },
  { id: 'holo', name: 'Holo', tier: 2, multiplier: 2.5, pack_weight: 50, pack_odds: 0.05, completeness_weight: 2 },
  { id: 'special', name: 'Special', tier: 3, multiplier: 3.5, pack_weight: 10, pack_odds: 0.01, completeness_weight: 3 },
]

export const PACK_TYPES: PackType[] = [
  { id: 'standard', name: 'Standard Pack', blurb: 'The everyday pack. Base odds.', cost: 210, size: 7, hue: 230, odds: {} },
  { id: 'deluxe', name: 'Deluxe Pack', blurb: 'Better odds of a shine on every card.', cost: 280, size: 7, hue: 280, odds: {} },
  { id: 'premium', name: 'Premium Pack', blurb: 'Ten cards and the best odds for Holo and Special.', cost: 450, size: 10, hue: 40, odds: {} },
]
