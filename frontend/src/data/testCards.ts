/** Cards for unit tests only (the app itself never invents cards). */

import type { Card } from './types.ts'

export function makeTestCard(over: Partial<Card> = {}): Card {
  return {
    id: 1, title: 'Card', kind: 'image', imageUrl: '', treatment: 'normal', tags: [], copies: 0, addedAt: '2026-01-01',
    thumbUrl: null, fileUrl: null, owned: {}, tagIds: [], reviewAvailable: false, reviewReward: null, upgradeCost: null,
    sellPrices: {}, sellExtras: {}, copiesByTreatment: {}, value: 0, printRun: 0, printTier: '', trend: 0, wanted: false, fuseFrom: null, fuseCopies: 3, number: 0, code: '', prices: {}, plainPrices: {}, saleQuotes: {}, fuseOptions: [], nextTagReward: 0, minPack: null, titleCustom: false, fileName: '',
    ...over,
  }
}

/** n cards with a repeating mix of treatments, tags and copies. */
export function makeTestCards(n: number): Card[] {
  const treatments = ['normal', 'normal', 'foil', 'holo', 'special']
  return Array.from({ length: n }, (_, i) => makeTestCard({
    id: i + 1,
    title: `Card ${i + 1}`,
    treatment: treatments[i % treatments.length],
    tags: i % 3 === 0 ? [] : ['pets'],
    copies: i % 4,
  }))
}
