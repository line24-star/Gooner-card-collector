/** Opening packs out of the inventory: the one place that wires the overlay to the store. */

import { fetchCard, fuseCard } from '../data/api.ts'
import { toastMilestones } from '../ui/toast.ts'
import {
  applySlots, getState, openPack, packCost, refreshWallet, replaceCard, revealVersion, settleVersions, takePackMilestones,
} from '../data/store.ts'
import type { PackType } from '../data/types.ts'
import { openInspect, wantedText } from '../pages/inspect.ts'
import { confirmDialog, fuseDialog } from '../ui/confirm.ts'
import { openPacks } from './opening.ts'

export const sealedOf = (packId: string): number => getState().wallet.inventory[packId] ?? 0

/**
 * Open `count` sealed packs of this kind in the overlay. Afterwards the overlay offers "Open
 * another" while sealed packs are left, and "Buy and open another" when none are: only that
 * button buys.
 */
export function openFromInventory(pack: PackType, count: number, onClose: () => void): void {
  openPacks({
    pack,
    count: Math.max(1, Math.min(count, sealedOf(pack.id) || 1)),
    price: () => packCost(pack),
    coins: () => getState().wallet.coins,
    sealed: () => sealedOf(pack.id),
    treatmentName: (id) => getState().treatments.find((t) => t.id === id)?.name ?? id,
    fxIntensity: fxIntensity(pack.id),
    fuseCopies: getState().wallet.fuseCopies,
    fuse: async (id, treatment) => {
      // A merge that uses the last copies of a Foil, Holo or Special empties that slot: ask first,
      // with the card as the store has it now (its count moves with the price).
      const card = getState().cards.find((c) => c.id === id)
      const option = card?.fuseOptions.find((o) => o.from === treatment)
      if (card && option?.empties && !(await confirmDialog(fuseDialog(card, option)))) return null
      try {
        const r = await fuseCard(id, treatment)
        applySlots(r.slots)
        toastMilestones(r.milestones ?? [])
        return r.treatment
      } finally {
        // Also after a refusal: the card on screen was out of date (its fuse count changed), so fetch it again.
        await Promise.all([fetchCard(id).then(replaceCard), refreshWallet()]).catch(() => {})
      }
    },
    milestones: takePackMilestones,
    // The run without a Holo or Special as the wallet has it before the next pack is opened: it only sets how the backs glow.
    dryStreak: () => getState().wallet.dryStreak,
    versions: () => ({ filled: getState().wallet.slotsFilled, total: getState().wallet.slotsTotal }),
    revealVersion,
    settleVersions,
    // The card panel is a modal dialog: it opens above the overlay, and closing it leaves the overlay as it was.
    inspect: (id, price) => openInspect(id, undefined, price ? { focus: 'price' } : {}),
    wantedText: (card) => wantedText(card, card.treatment),
    freePack: () => (getState().wallet.freePack.available ? 'ready' : null),
    nextTreatment: (id) => {
      const tiers = [...getState().treatments].sort((a, b) => a.tier - b.tier)
      const i = tiers.findIndex((t) => t.id === id)
      return i >= 0 && i + 1 < tiers.length ? tiers[i + 1].id : null
    },
    nextPack: () => openPack(pack.id, sealedOf(pack.id) === 0, undefined, true),
    onClose,
  })
}

/** Dearer packs get bigger effects: 1 for the cheapest pack, up to 1.6 for the dearest. */
export function fxIntensity(packId: string): number {
  const ladder = [...getState().packTypes].sort((a, b) => a.cost - b.cost)
  const i = ladder.findIndex((p) => p.id === packId)
  return i < 1 || ladder.length < 2 ? 1 : 1 + 0.6 * (i / (ladder.length - 1))
}
