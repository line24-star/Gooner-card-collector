import { describe, expect, it } from 'vitest'
import { reasonLabel } from '../data/accountApi.ts'
import { makeTestCard } from '../data/testCards.ts'
import type { PackType, PulledCard, TreatmentInfo } from '../data/types.ts'
import { signed } from '../pages/account.ts'
import { packOdds, pct } from '../pages/packs.ts'
import { maxTilt, tiltFor } from '../cards/card.ts'
import { clampBuy, exclusiveText, oneIn, perPack } from '../pages/packs.ts'
import { packsFrom } from '../pages/inspect.ts'
import { lightboxHTML } from '../ui/lightbox.ts'
import {
  coinEffect, filledSlots, flipTiming, fuseFailText, glowLevel, nearMissNote, nextSteps, primaryAction, pullBadge, pullNote, pullStatus,
  revealCols, summarize, summaryText, valueNote, versionLine,
} from './opening.ts'
import { wantedText } from '../pages/inspect.ts'
import { ApiError } from '../data/api.ts'

const pull = (outcome: PulledCard['outcome'], over: Partial<PulledCard> = {}): PulledCard => ({
  card: makeTestCard({ treatment: 'foil' }), isNew: outcome === 'new', nearMiss: false, outcome, coins: 0, value: 0, ...over,
})

describe('pack summary', () => {
  it('counts each outcome and adds up the coins from doubles', () => {
    const s = summarize([pull('new', { value: 4 }), pull('new', { value: 6 }), pull('better', { value: 30 }), pull('upgraded'), pull('duplicate'), pull('duplicate')])
    expect(s).toEqual({ fresh: 2, better: 1, versions: 0, normals: 0, upgraded: 1, duplicates: 2, coins: 0, value: 40 })
    expect(summaryText(s)).toBe('2 new cards · 1 rarer copy · 1 upgrade · 2 duplicates · worth 🪙 40 together')
  })
  it('leaves out what did not happen', () => {
    expect(summaryText(summarize([pull('new')]))).toBe('1 new card')
    expect(summaryText(summarize([pull('duplicate')]))).toBe('1 duplicate')
  })
})

describe('pullNote', () => {
  const name = (id: string) => id.toUpperCase()
  it('says what the pull did', () => {
    expect(pullNote(pull('new'))).toBe('New card')
    expect(pullNote(pull('better'), name)).toBe('Rarer copy: FOIL')
    expect(pullNote(pull('upgraded'), name)).toBe('Duplicate → upgraded to FOIL')
    expect(pullNote(pull('duplicate'))).toBe('Duplicate: an extra copy to sell or merge')
    expect(pullNote(pull('duplicate'))).not.toMatch(/fuse/i)
    expect(pullNote(pull('duplicate'))).not.toMatch(/double/i)
  })
  it('a near miss names what was almost pulled, and only below Holo', () => {
    const normal = pull('new', { nearMiss: true, card: makeTestCard({ treatment: 'normal' }) })
    expect(nearMissNote(normal, 'Foil')).toBe('Almost a Foil!')
    expect(nearMissNote(pull('new', { nearMiss: true }), 'Holo')).toBe('Almost a Holo!') // a Foil pull
    const holo = pull('new', { nearMiss: true, card: makeTestCard({ treatment: 'holo' }) })
    expect(nearMissNote(holo, 'Special')).toBe('') // a Holo is already the win
    expect(nearMissNote(pull('new'), 'Holo')).toBe('')
  })
  it('shows what the pulled copy is worth', () => {
    expect(valueNote(pull('new', { value: 1234 }))).toMatch(/^🪙 1[.,]234$/)
    expect(valueNote(pull('new'))).toBe('')
  })
})

describe('shop odds', () => {
  const foil: TreatmentInfo = { id: 'foil', name: 'Foil', tier: 1, multiplier: 1.5, pack_weight: 160, pack_odds: 0.16 }
  const pack: PackType = { id: 'premium', name: 'Premium', blurb: '', cost: 450, size: 10, hue: 40, odds: { foil: 0.252 } }
  it('uses the pack own odds, and the base odds when a pack has none', () => {
    expect(packOdds(pack, foil)).toBe(0.252)
    expect(packOdds({ ...pack, odds: {} }, foil)).toBe(0.16)
  })
  it('shows one decimal only for small chances', () => {
    expect(pct(0.252)).toBe('25%')
    expect(pct(0.063)).toBe('6.3%')
    expect(pct(0.01)).toBe('1.0%')
  })
})

describe('full-screen viewer', () => {
  it('shows the card itself, not a loose picture', () => {
    const html = lightboxHTML(makeTestCard({ fileUrl: '/api/media/7/file', thumbUrl: '/t' }))
    expect(html).toContain('class="card ')
    expect(html).toContain('card-xl')
    expect(html).toContain('src="/api/media/7/file"')
    expect(html).not.toContain('lightbox-media')
    expect(html).toContain('draggable="false"')
  })
  it('a video card plays in a loop inside the card, without a player', () => {
    const html = lightboxHTML(makeTestCard({ kind: 'video', fileUrl: '/api/media/7/file' }))
    expect(html).toContain('<video autoplay loop ')
    expect(html).not.toContain('controls')
    expect(html).not.toContain('muted') // full screen plays with sound
    expect(html).toContain('data-act="sound"')
  })
  it('escapes the url and title', () => {
    const html = lightboxHTML(makeTestCard({ fileUrl: '/x" onload="alert(1)', title: '<b>' }))
    expect(html).not.toContain('" onload="alert(1)')
    expect(html).not.toContain('<b>')
  })
})

describe('reveal layout', () => {
  it('one row up to five cards, two rows above', () => {
    expect([1, 5, 6, 7, 10].map(revealCols)).toEqual([1, 5, 3, 4, 5])
  })
})

describe('tilt', () => {
  it('a bigger card leans less', () => {
    expect(maxTilt(168)).toBeGreaterThan(maxTilt(360))
    expect(maxTilt(360)).toBeGreaterThan(maxTilt(900))
  })
  it('is flat in the middle and clamped at the edges', () => {
    expect(tiltFor(0.5, 0.5, 168)).toEqual({ rx: 0, ry: 0, mx: 50, my: 50 })
    expect(tiltFor(5, -3, 168)).toEqual(tiltFor(1, 0, 168)) // a pointer outside the box never over-rotates
    expect(Math.abs(tiltFor(1, 0, 900).rx)).toBeLessThanOrEqual(3)
  })
})

describe('buy X', () => {
  it('never goes above what the coins buy, or below one', () => {
    expect(clampBuy('7', 12)).toBe(7)
    expect(clampBuy('999', 12)).toBe(12) // typed more than the coins buy: the most they buy
    expect(clampBuy('0', 12)).toBe(1)
    expect(clampBuy('-3', 12)).toBe(1)
    expect(clampBuy('abc', 12)).toBe(1)
    expect(clampBuy('2.9', 12)).toBe(2)
    expect(clampBuy('5', 0)).toBe(0) // can't afford any
  })
})

describe('cards only in the dearer packs', () => {
  const packs = [{ id: 's', name: 'Standard Pack', cost: 100 }, { id: 'd', name: 'Deluxe Pack', cost: 180 }, { id: 'p', name: 'Premium Pack', cost: 480 }]
  const ex = { s: { cards: 0, missing: 0 }, d: { cards: 12, missing: 9 }, p: { cards: 1, missing: 0 } }
  it('says which packs hold them and how many are left', () => {
    expect(exclusiveText(packs[0], packs, ex)).toBe('')
    expect(exclusiveText(packs[1], packs, ex)).toBe('12 cards are only in Deluxe and Premium Packs · 9 still to pull')
    expect(exclusiveText(packs[2], packs, ex)).toBe('1 card is only in Premium Packs · you have them all')
  })
  it('names the pack on the card', () => {
    expect(packsFrom('d', packs)).toBe('Deluxe Packs and up')
    expect(packsFrom('p', packs)).toBe('Premium Packs')
  })
})

describe('odds per pack', () => {
  const foil: TreatmentInfo = { id: 'foil', name: 'Foil', tier: 1, multiplier: 1.5, pack_weight: 770, pack_odds: 0.077 }
  const std: PackType = { id: 'standard', name: 'Standard', blurb: '', cost: 100, size: 5, hue: 1, odds: {} }
  it('turns per-card odds into the chance for a whole pack', () => {
    expect(perPack(std, foil)).toBeCloseTo(1 - 0.923 ** 5, 5)
    expect(oneIn(perPack(std, foil))).toBe('1 in 3 packs')
    expect(oneIn(0.0185)).toBe('1 in 54 packs')
    expect(oneIn(0.87)).toBe('87% of packs')
  })
})

describe('player page text', () => {
  it('names ledger reasons in plain language', () => {
    expect(reasonLabel('starting_grant')).toBe('Welcome coins')
    expect(reasonLabel('upgrade_attempt')).toBe('Upgrade attempt')
    expect(reasonLabel('some_new_reason')).toBe('Some new reason')
  })
  it('signs amounts', () => {
    expect(signed(12)).toBe('+12')
    expect(signed(-210)).toBe('-210')
  })
})

describe('v0.7.1: a new version is not a duplicate (L6)', () => {
  it('has its own note, badge and place in the summary', () => {
    const p = pull('version', { card: makeTestCard({ treatment: 'foil' }), value: 12 })
    expect(pullNote(p, (id) => id.toUpperCase())).toBe('New version: FOIL')
    expect(pullBadge(p)).toBe('NEW')
    expect(pullBadge(pull('new'))).toBe('NEW')
    expect(pullBadge(pull('duplicate'))).toBeUndefined()
    const s = summarize([p, pull('version'), pull('duplicate')])
    expect(s).toMatchObject({ versions: 2, duplicates: 1 })
    expect(summaryText(s)).toBe('2 new versions · 1 duplicate · worth 🪙 12 together')
    expect(summaryText(summarize([p]))).toBe('1 new version · worth 🪙 12 together')
  })
})

describe('v0.7.1: a refused fuse says why (D3)', () => {
  it("shows the server's reason, and the old line only when there is none", () => {
    expect(fuseFailText('Trip #0008', new ApiError(409, 'Fusing needs 4 copies, you have 3'))).toBe('Trip #0008: Fusing needs 4 copies, you have 3')
    expect(fuseFailText('Trip #0008', new TypeError('Failed to fetch'))).toBe('That did not work. Try again.')
    expect(fuseFailText('Trip #0008', new ApiError(500, 'boom'))).toBe('That did not work. Try again.')
  })
})

describe('v0.8.0: the version line and the slots a pack filled (4.1)', () => {
  it('leads with the versions of the pack just opened', () => {
    expect(versionLine(0)).toBe('No new versions')
    expect(versionLine(1)).toBe('+1 version')
    expect(versionLine(2)).toBe('+2 versions')
  })
  it('lists the filled slots by card name and treatment, in pull order', () => {
    const pulls = [
      pull('new', { card: makeTestCard({ id: 1, title: 'Sea', treatment: 'normal' }) }),
      pull('new', { fillsSlot: true, card: makeTestCard({ id: 2, title: 'Sun', treatment: 'foil' }) }),
      pull('duplicate', { alreadyOwned: true, card: makeTestCard({ id: 3, title: 'Top', treatment: 'foil' }) }),
      pull('version', { fillsSlot: true, card: makeTestCard({ id: 4, title: 'Bay', treatment: 'holo' }) }),
    ]
    expect(filledSlots(pulls)).toEqual([{ id: 2, title: 'Sun', treatment: 'foil' }, { id: 4, title: 'Bay', treatment: 'holo' }])
    expect(versionLine(filledSlots(pulls).length)).toBe('+2 versions')
  })
})

describe('v0.8.0: a first Normal copy is not a version', () => {
  it('has its own words, no NEW badge and the duplicate colour: the version line would say "No new versions"', () => {
    const p = pull('version', { card: makeTestCard({ treatment: 'normal' }), value: 5 })
    expect(pullNote(p, (id) => id)).toBe('First normal copy')
    expect(pullBadge(p)).toBeUndefined()
    expect(pullStatus(p)).toBe('duplicate')
    expect(summaryText(summarize([p, p]))).toBe('2 first Normal copies · worth 🪙 10 together')
    expect(summaryText(summarize([p]))).not.toMatch(/version/)
  })
})

describe('v0.8.0: "Already owned" (4.5)', () => {
  it('is appended to a duplicate whose treatment was held while a higher slot is empty', () => {
    expect(pullNote(pull('duplicate', { alreadyOwned: true }))).toBe('Already owned — slot still empty') // v0.9.0: the spec's wording
    expect(pullNote(pull('duplicate', { alreadyOwned: true, coins: 3 }))).toBe('Already owned — slot still empty · +3 coins')
    expect(pullNote(pull('duplicate'))).not.toContain('Already owned')
    expect(pullNote(pull('version', { alreadyOwned: true }))).not.toContain('Already owned') // the server never sends both
  })
})

describe('v0.8.0: the status glow (PT11)', () => {
  it('has one status per kind of pull', () => {
    expect(pullStatus(pull('new'))).toBe('new')
    expect(['better', 'version', 'upgraded'].map((o) => pullStatus(pull(o as PulledCard['outcome'])))).toEqual(['version', 'version', 'version'])
    expect(pullStatus(pull('duplicate'))).toBe('duplicate')
  })
  it('grows with the treatment', () => {
    expect(['normal', 'foil', 'holo', 'special'].map(glowLevel)).toEqual([1, 2, 3, 4])
    expect(glowLevel('something-else')).toBe(1)
  })
})

describe('v0.8.0: the coin effect under a card (PT8)', () => {
  it('a 6-coin card ticks, a 600-coin card bursts', () => {
    expect(coinEffect(6)).toEqual({ level: 0, coins: 0, ms: 250 })
    expect(coinEffect(600).level).toBe(3)
    expect(coinEffect(600).coins).toBeGreaterThan(coinEffect(100).coins)
    expect(coinEffect(100).coins).toBeGreaterThan(coinEffect(30).coins)
  })
  it('steps at 20, 60 and 180 coins and never shrinks with the value', () => {
    expect([19, 20, 59, 60, 179, 180].map((v) => coinEffect(v).level)).toEqual([0, 1, 1, 2, 2, 3])
    let last = coinEffect(1)
    for (let v = 2; v <= 5000; v += 7) {
      const e = coinEffect(v)
      expect(e.coins).toBeGreaterThanOrEqual(last.coins)
      expect(e.ms).toBeGreaterThanOrEqual(last.ms)
      last = e
    }
    expect(coinEffect(100000).coins).toBeLessThanOrEqual(36)
    expect(coinEffect(0)).toEqual({ level: 0, coins: 0, ms: 0 })
  })
})

describe('v0.8.0: every card turns in its own time (PT14, UR3)', () => {
  const lo = () => 0
  const hi = () => 1
  it('varies a little per card', () => {
    expect(flipTiming({ level: 0 }, lo).turn).toBeLessThan(flipTiming({ level: 0 }, hi).turn)
    expect(flipTiming({ level: 0 }, hi).turn - flipTiming({ level: 0 }, lo).turn).toBeLessThanOrEqual(200)
    expect(flipTiming({ level: 0 }, lo).hold).toBe(0)
    expect(flipTiming({ level: 1 }, lo).hold).toBe(0)
  })
  it('a Holo, a Special and a card that fills a slot hold a beat and turn slower', () => {
    const normal = flipTiming({ level: 0 }, hi)
    for (const t of [flipTiming({ level: 2 }, lo), flipTiming({ level: 3 }, lo), flipTiming({ level: 0, fills: true }, lo), flipTiming({ level: 1, fills: true }, lo)]) {
      expect(t.hold).toBeGreaterThanOrEqual(240)
      expect(t.turn).toBeGreaterThan(normal.turn)
    }
    expect(flipTiming({ level: 3 }, lo).hold).toBeGreaterThan(flipTiming({ level: 2 }, hi).hold)
    expect(flipTiming({ level: 3, fills: true }, lo)).toEqual(flipTiming({ level: 3 }, lo)) // never shorter for filling
  })
  it('from the third pack in a row the commons turn faster, and only the commons', () => {
    expect(flipTiming({ level: 0, packNo: 2 }, hi)).toEqual(flipTiming({ level: 0 }, hi))
    expect(flipTiming({ level: 0, packNo: 3 }, hi).turn).toBeLessThan(flipTiming({ level: 0, packNo: 2 }, lo).turn)
    expect(flipTiming({ level: 0, packNo: 3, fills: true }, lo)).toEqual(flipTiming({ level: 0, fills: true }, lo))
    expect(flipTiming({ level: 2, packNo: 9 }, lo)).toEqual(flipTiming({ level: 2 }, lo))
  })
  it('"Reveal all" is fast and reduced motion has no hold and no variation', () => {
    expect(flipTiming({ level: 3, fills: true, quiet: true }, hi)).toEqual({ hold: 0, turn: 320 })
    expect(flipTiming({ level: 3, fills: true, reduced: true }, lo)).toEqual(flipTiming({ level: 0, reduced: true }, hi))
    expect(flipTiming({ level: 3, reduced: true }).hold).toBe(0)
  })
})

describe('v0.8.0: the main button under a finished pack (4.2)', () => {
  const base = { remaining: 0, sealed: 0, coins: 0, price: 100 }
  it('opens another whenever that is possible', () => {
    expect(primaryAction({ ...base, remaining: 4, sealed: 4 })).toEqual({ kind: 'next', label: 'Next pack (4 to go)' })
    expect(primaryAction({ ...base, sealed: 2 })).toEqual({ kind: 'another', label: 'Open another · 2 sealed' })
    expect(primaryAction({ ...base, coins: 100 }).label).toBe('Buy and open another · 🪙 100')
    expect(primaryAction({ ...base, sealed: 1, coins: 500 }).kind).toBe('another') // a sealed pack before buying one
  })
  it('never says Close or Done: without a pack it points at a way to get one', () => {
    expect(primaryAction({ ...base, coins: 99, freeReady: true })).toEqual({ kind: 'free', label: 'Claim your free pack' })
    expect(primaryAction({ ...base, coins: 99 })).toEqual({ kind: 'earn', label: 'Tag these cards to earn coins' })
    for (const o of [base, { ...base, sealed: 3 }, { ...base, coins: 900 }, { ...base, remaining: 1 }]) expect(primaryAction(o).label).not.toMatch(/close|done/i)
  })
})

describe('v0.8.0: the prompts after a pack (PT9, Merge)', () => {
  const name = (id: string) => id.toUpperCase()
  const next = (id: string) => (id === 'normal' ? 'foil' : null)
  it('a merge uses the word Merge and the card own count', () => {
    const card = makeTestCard({ id: 1, title: 'Sea', fuseFrom: 'normal', fuseCopies: 3, fuseOptions: [{ from: 'normal', to: 'foil', copies: 5, have: 5, can: true, net_versions: 1, empties: false, fills: true, value: 150 }] })
    const steps = nextSteps([pull('duplicate', { card })], 3, name, next)
    expect(steps[0].label).toBe('Merge 5 NORMAL → FOIL: Sea')
    expect(steps[0].label).not.toMatch(/fuse/i)
  })
  it('a wanted card says what the bonus is in coins', () => {
    const card = makeTestCard({ id: 2, title: 'Sun', treatment: 'normal', wanted: true, sellPrices: { normal: 31 }, plainPrices: { normal: 24 } })
    const steps = nextSteps([pull('new', { card })], 3, name, next, 3, (c) => wantedText(c, c.treatment))
    expect(steps[0].label).toBe('★ Sun is wanted: sells for 🪙 31 instead of 24')
    const plain = makeTestCard({ id: 3, title: 'Top', wanted: false, sellPrices: { normal: 7 }, plainPrices: { normal: 7 } })
    expect(nextSteps([pull('new', { card: plain })], 3, name, next, 3, (c) => wantedText(c, c.treatment))).toEqual([])
  })
})
