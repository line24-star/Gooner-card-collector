import { describe, expect, it } from 'vitest'
import { makeTestCard } from '../data/testCards.ts'
import type { FuseOption } from '../data/types.ts'
import { confirmHTML, duplicatesDialog, emptiesText, fuseDialog, netVersionsText, offerDialog, saleCoins, saleDialog } from './confirm.ts'
import { scanAddedText, scanQuestion } from './scanFlow.ts'

const n = (x: number) => x.toLocaleString()

describe('the sale question (5.1): only a sale that loses something asks', () => {
  const card = makeTestCard({
    title: 'Sunset', copies: 4, copiesByTreatment: { normal: 2, foil: 1, holo: 1 },
    sellPrices: { normal: 7, foil: 31, holo: 1200 }, saleQuotes: { normal: [7, 14], foil: [31], holo: [1200] },
  })
  it('a plain sale goes ahead at once', () => {
    expect(saleDialog(card, 'normal', 1)).toBeNull()
    expect(saleDialog(card, 'normal', 2)).toBeNull() // the last Normal copies: Normal is not a version, the card stays
  })
  it('the last copy of a Foil, Holo or Special shows the coins and the slot with equal weight', () => {
    const q = saleDialog(card, 'foil', 1)!
    expect(q.title).toBe('Sell your last Foil copy of Sunset?')
    expect(q.rows).toEqual([
      { label: 'You receive', value: '🪙 31', tone: 'gain' },
      { label: 'You lose', value: 'This will empty the Foil slot (−1 version)', tone: 'loss', color: 'var(--r-foil)' },
    ])
    expect(q.confirm).toBe('Sell · 🪙 31')
    expect(saleDialog(card, 'holo', 1)!.rows![0].value).toBe(`🪙 ${n(1200)}`)
  })
  it('the last copy of all says that the card leaves the collection', () => {
    const last = makeTestCard({ title: 'Sea', copies: 1, copiesByTreatment: { normal: 1 }, sellPrices: { normal: 5 } })
    const q = saleDialog(last, 'normal', 1)!
    expect(q.rows![1].value).toBe('The card leaves your collection until you pull it again')
    const lastFoil = makeTestCard({ title: 'Sea', copies: 1, copiesByTreatment: { foil: 1 }, sellPrices: { foil: 9 } })
    expect(saleDialog(lastFoil, 'foil', 1)!.rows![1].value)
      .toBe('This will empty the Foil slot (−1 version). The card leaves your collection until you pull it again')
  })
  it('names the coins from the server quote, else price times count', () => {
    expect(saleCoins(card, 'normal', 2)).toBe(14)
    expect(saleCoins({ saleQuotes: {}, sellPrices: { normal: 7 } }, 'normal', 3)).toBe(21)
  })
})

describe('the merge question (5.2, amendment 2): the real net change', () => {
  const opt = (o: Partial<FuseOption>): FuseOption => ({ from: 'foil', to: 'holo', copies: 3, have: 3, can: true, net_versions: 0, empties: true, fills: true, value: 93, ...o })
  it('lists what goes, what comes, the net versions and the value given up', () => {
    const q = fuseDialog({ title: 'Sunset' }, opt({}))
    expect(q.title).toBe('Merge 3 Foil copies of Sunset?')
    expect(q.rows!.map((r) => [r.label, r.value])).toEqual([
      ['You give up', '3 Foil copies, worth 🪙 93 on the market'],
      ['You get', '1 Holo copy'],
      ['Versions', 'No change (0 versions)'],
      ['You lose', 'This will empty the Foil slot'],
    ])
    expect(q.note).toBe('The Holo copy fills its empty slot.')
  })
  it('shows −1 when the last copies go into a treatment already held', () => {
    const q = fuseDialog({ title: 'Sunset' }, opt({ net_versions: -1, fills: false, copies: 5, value: 400 }))
    expect(q.rows![2]).toMatchObject({ value: '−1 version', tone: 'loss' })
    expect(q.note).toBe('You already hold a Holo: the new copy fills no slot.')
    expect(q.title).toContain('Merge 5 Foil')
  })
  it('words the net change', () => {
    expect(netVersionsText(1)).toBe('+1 version')
    expect(netVersionsText(-1)).toBe('−1 version')
    expect(netVersionsText(2)).toBe('+2 versions')
    expect(netVersionsText(0)).toBe('No change (0 versions)')
    expect(emptiesText('special')).toBe('This will empty the Special slot (−1 version)')
  })
})

describe('the other questions', () => {
  it('sell all duplicates: the coins, and that no slot empties', () => {
    const q = duplicatesDialog(12, 1234)
    expect(q.title).toBe('Sell 12 duplicate copies?')
    expect(q.rows![0].value).toBe(`🪙 ${n(1234)}`)
    expect(q.rows![1].value).toMatch(/no version slot empties/)
  })
  it("a collector's offer on an only copy (7.2)", () => {
    const q = offerDialog({ title: 'Sunset', treatment: 'holo', price: 300, emptiesSlot: true, lastCopy: false })
    expect(q.rows![1].value).toBe('Accepting empties the Holo slot (−1 version)')
    expect(q.confirm).toBe('Take it · 🪙 300')
  })
  it('the dialog markup escapes its texts and has both buttons', () => {
    const html = confirmHTML({ title: 'Sell <b>?', rows: [{ label: 'You receive', value: '🪙 5', tone: 'gain' }], confirm: 'Sell' })
    expect(html).not.toContain('<b>?')
    expect(html).toContain('data-confirm="yes"')
    expect(html).toContain('data-confirm="no"')
    expect(html).toContain('is-gain')
  })
})

describe('the scan (6.1, 6.2)', () => {
  const preview = {
    new_media: 12, restored_media: 0, missing_media: 0, new_slots: 36,
    slots_filled: 41, slots_total: 180, slots_filled_after: 41, slots_total_after: 216,
    completeness_now: 41 / 180, completeness_after: 41 / 216,
  }
  it('asks before adding media to a library that has cards', () => {
    const q = scanQuestion(preview)!
    expect(q.text).toBe('Adding 12 media will add 36 version slots. Versions: 23% → 19%')
    expect(q.confirm).toBe('Scan')
    expect(q.cancel).toBe('Cancel')
  })
  it('does not ask without new files, or on the first import into an empty library', () => {
    expect(scanQuestion({ ...preview, new_media: 0, new_slots: 0 })).toBeNull()
    expect(scanQuestion({ ...preview, slots_total: 0, slots_filled: 0 })).toBeNull()
  })
  it('says what a scan added', () => {
    expect(scanAddedText({ new_media: 12, new_slots: 36 })).toBe('+12 media · +36 version slots')
    expect(scanAddedText({ new_media: 2 })).toBe('+2 media · +6 version slots')
    expect(scanAddedText({ new_media: 0 })).toBeNull()
    expect(scanAddedText(null)).toBeNull()
  })
})
