/** Pack odds as the shop and the card panel say them. Pure: the numbers come from the server config. */

import type { PackType, TreatmentInfo } from './types.ts'

/** This pack's chance per card for a treatment: its own odds when the server sent them, else the base odds. */
export function packOdds(pack: Pick<PackType, 'odds'>, t: Pick<TreatmentInfo, 'id' | 'pack_odds'>): number {
  return pack.odds[t.id] ?? t.pack_odds
}

/** The chance that a whole pack holds at least one card of this treatment. */
export function perPack(pack: Pick<PackType, 'odds' | 'size'>, t: Pick<TreatmentInfo, 'id' | 'pack_odds'>): number {
  return 1 - (1 - packOdds(pack, t)) ** pack.size
}

export function pct(x: number): string {
  return `${(x * 100).toFixed(x < 0.1 ? 1 : 0)}%`
}

/** "1 in 3 packs" style odds; below 1 in 2 it reads better as a percentage of packs. */
export function oneIn(p: number): string {
  if (p <= 0) return 'never'
  if (p >= 0.5) return `${Math.round(p * 100)}% of packs`
  return `1 in ${Math.round(1 / p)} packs`
}

/** The packs that hold a card, cheapest first: every pack, or `minPack` and the dearer ones. */
export function packsHolding<P extends { id: string; cost: number }>(minPack: string | null, packs: P[]): P[] {
  const sorted = [...packs].sort((a, b) => a.cost - b.cost)
  const from = minPack ? sorted.findIndex((p) => p.id === minPack) : 0
  return from > 0 ? sorted.slice(from) : sorted
}
