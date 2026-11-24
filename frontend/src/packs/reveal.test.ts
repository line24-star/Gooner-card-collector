/** v0.9.0: the Anticipation and Reveal Feedback Spec, as the pack overlay's pure rules (rows A1 to A4, A8, A9; decision D6). */
import { describe, expect, it } from 'vitest'
import { makeTestCard } from '../data/testCards.ts'
import type { PulledCard } from '../data/types.ts'
import { MAX_SFX_MS, sfxDuration } from '../fx/sound.ts'
import {
  ALREADY_OWNED, DRY_GLOW_MAX, dryGlowLevel, firstFlipHold, FIRST_FLIP_HOLD_MS, FIRST_FLIP_HOLD_REDUCED_MS, flipTiming, fxLevel,
  GLOW_MAX_MS, GLOW_MIN_MS, GLOW_REDUCED_MS, glowTime, pullNote, pullStatus, revealSounds, showsHint, tierHint, versionLine,
} from './opening.ts'

const pull = (outcome: PulledCard['outcome'], over: Partial<PulledCard> = {}): PulledCard => ({
  card: makeTestCard({ treatment: 'foil' }), isNew: outcome === 'new', nearMiss: false, outcome, coins: 0, value: 0, ...over,
})
const lo = () => 0
const hi = () => 1

describe('the face-down hold before the first flip (A1)', () => {
  it('is 800 ms', () => {
    expect(FIRST_FLIP_HOLD_MS).toBe(800)
    expect(firstFlipHold()).toBe(800)
    expect(firstFlipHold(false)).toBe(800)
  })
  it('is shorter with reduced motion, never gone (A9)', () => {
    expect(firstFlipHold(true)).toBe(FIRST_FLIP_HOLD_REDUCED_MS)
    expect(firstFlipHold(true)).toBeGreaterThan(0)
    expect(firstFlipHold(true)).toBeLessThan(firstFlipHold(false))
  })
})

describe('the glow before each flip (A1)', () => {
  it('is 400 to 800 ms, whatever the random number', () => {
    expect(GLOW_MIN_MS).toBe(400)
    expect(GLOW_MAX_MS).toBe(800)
    for (const packNo of [1, 2, 3, 10]) {
      for (const rand of [lo, hi, () => 0.5, () => -3, () => 7, Math.random]) {
        const ms = glowTime({ packNo }, rand)
        expect(ms).toBeGreaterThanOrEqual(GLOW_MIN_MS)
        expect(ms).toBeLessThanOrEqual(GLOW_MAX_MS)
      }
    }
  })
  it('the first pack uses the long half, and varies', () => {
    expect(glowTime({ packNo: 1 }, lo)).toBe(600)
    expect(glowTime({ packNo: 1 }, hi)).toBe(800)
    expect(glowTime({}, lo)).toBe(600) // no pack number: the first pack
  })
  it('later packs of a multi-pack run use the short end', () => {
    expect(glowTime({ packNo: 2 }, hi)).toBe(GLOW_MIN_MS)
    expect(glowTime({ packNo: 9 }, hi)).toBe(GLOW_MIN_MS)
    expect(glowTime({ packNo: 2 }, hi)).toBeLessThan(glowTime({ packNo: 1 }, lo))
  })
  it('reduced motion shortens it, it stays above zero (A9)', () => {
    expect(glowTime({ packNo: 1, reduced: true }, hi)).toBe(GLOW_REDUCED_MS)
    expect(GLOW_REDUCED_MS).toBeGreaterThan(0)
    expect(GLOW_REDUCED_MS).toBeLessThan(GLOW_MIN_MS)
  })
  it('"Reveal all" adds no glow of its own', () => {
    expect(glowTime({ packNo: 1, quiet: true }, hi)).toBe(0)
  })
  it('the outcome does not change the glow: it cannot give the card away', () => {
    // glowTime takes no pull at all; the emphasis by outcome is in the turn (flipTiming), after the glow
    expect(glowTime.length).toBeLessThanOrEqual(2)
    expect(flipTiming({ level: 0, fills: true }, lo).turn).toBeGreaterThan(flipTiming({ level: 0 }, lo).turn)
  })
})

describe('the face-down glow is the hint, not the truth (A2, P6, D6)', () => {
  it('follows `hint` only', () => {
    expect(showsHint({ hint: true })).toBe(true)
    expect(showsHint({ hint: false })).toBe(false)
    expect(showsHint({})).toBe(false)
  })
  it('a card that fills a slot may not glow, and a card that glows may fill nothing', () => {
    const quietFill = pull('version', { fillsSlot: true, hint: false })
    const falseGlow = pull('duplicate', { fillsSlot: false, hint: true })
    expect(showsHint(quietFill)).toBe(false)
    expect(showsHint(falseGlow)).toBe(true)
  })
  it('the glow of every back grows with the dry streak, in steps, to a cap', () => {
    expect(dryGlowLevel(0)).toBe(0)
    expect(dryGlowLevel(2)).toBe(0)
    expect(dryGlowLevel(3)).toBe(1)
    expect(dryGlowLevel(6)).toBe(2)
    expect(dryGlowLevel(11)).toBe(3)
    expect(dryGlowLevel(12)).toBe(DRY_GLOW_MAX)
    expect(dryGlowLevel(400)).toBe(DRY_GLOW_MAX)
    for (let s = 0; s < 40; s++) expect(dryGlowLevel(s + 1)).toBeGreaterThanOrEqual(dryGlowLevel(s))
  })
  it('a missing or broken streak is no glow', () => {
    expect(dryGlowLevel(-1)).toBe(0)
    expect(dryGlowLevel(Number.NaN)).toBe(0)
  })
  it('the print tier is the hint under a face-down card', () => {
    expect(tierHint(makeTestCard({ printTier: 'Limited' }))).toBe('Limited')
    expect(tierHint(makeTestCard({ printTier: '' }))).toBe('')
  })
})

describe('the per-card result text (A4)', () => {
  it('names the slot a card fills, with the server\'s name of the treatment', () => {
    expect(pullNote(pull('better', { fillsSlot: true, treatmentName: 'Foil' }))).toBe('Fills Foil slot')
    expect(pullNote(pull('version', { fillsSlot: true, treatmentName: 'Holo', card: makeTestCard({ treatment: 'holo' }) }))).toBe('Fills Holo slot')
    expect(pullNote(pull('version', { fillsSlot: true }), (id) => id.toUpperCase())).toBe('Fills FOIL slot') // no name sent: the page's own
  })
  it('a new card that fills a slot says both', () => {
    expect(pullNote(pull('new', { fillsSlot: true, treatmentName: 'Special' }))).toBe('New card · Fills Special slot')
    expect(pullNote(pull('new', { card: makeTestCard({ treatment: 'normal' }) }))).toBe('New card')
  })
  it('says "Already owned — slot still empty" for the near miss, in the calm colour of a duplicate', () => {
    const p = pull('duplicate', { alreadyOwned: true })
    expect(ALREADY_OWNED).toBe('Already owned — slot still empty')
    expect(pullNote(p)).toBe(ALREADY_OWNED)
    expect(pullStatus(p)).toBe('duplicate') // not an error state
  })
  it('a Normal duplicate is just a duplicate: the near-miss line is for a Foil or Holo held twice', () => {
    const p = pull('duplicate', { alreadyOwned: true, card: makeTestCard({ treatment: 'normal' }) })
    expect(pullNote(p)).toBe('Duplicate: an extra copy to sell or merge')
  })
})

describe('the version line counts up as cards are turned (A4 with D6)', () => {
  it('says nothing final while cards are face down', () => {
    expect(versionLine(0, false)).toBe('No new versions yet')
    expect(versionLine(1, false)).toBe('+1 version')
    expect(versionLine(2, false)).toBe('+2 versions')
  })
  it('is final when every card is up', () => {
    expect(versionLine(0, true)).toBe('No new versions')
    expect(versionLine(0)).toBe('No new versions')
    expect(versionLine(3)).toBe('+3 versions')
  })
})

describe('the escalation by outcome (A3)', () => {
  it('ordinary and duplicate: the base sound', () => {
    expect(revealSounds({ fillsSlot: false }, 0)).toEqual({ now: 'reveal-0', then: null })
  })
  it('a higher treatment already owned (a Foil that fills nothing): the base sound, a standard flip', () => {
    expect(revealSounds({ fillsSlot: false }, 1)).toEqual({ now: 'reveal-0', then: null })
    expect(fxLevel({ fillsSlot: false, isNew: false }, 1)).toBe(0)
    expect(flipTiming({ level: 1 }, hi).hold).toBe(0)
  })
  it('fills an empty slot: the slot-fill sound, and a slightly longer turn', () => {
    expect(revealSounds({ fillsSlot: true }, 1)).toEqual({ now: 'slot-fill', then: null })
    expect(revealSounds({ fillsSlot: true }, 0).now).toBe('slot-fill')
    expect(fxLevel({ fillsSlot: true, isNew: false }, 1)).toBe(1)
    const fill = flipTiming({ level: 1, fills: true }, lo)
    const plain = flipTiming({ level: 1 }, lo)
    expect(fill.hold + fill.turn).toBeGreaterThan(plain.hold + plain.turn)
  })
  it('a Holo or Special, even as a duplicate: the escalated sting, the screen effect, the longest turn', () => {
    expect(revealSounds({ fillsSlot: false }, 2)).toEqual({ now: 'reveal-2', then: null })
    expect(revealSounds({ fillsSlot: false }, 3)).toEqual({ now: 'reveal-3', then: null })
    expect(fxLevel({ fillsSlot: false, isNew: false }, 2)).toBe(2)
    expect(fxLevel({ fillsSlot: false, isNew: false }, 3)).toBe(3)
    const special = flipTiming({ level: 3 }, lo)
    const fill = flipTiming({ level: 1, fills: true }, hi)
    expect(special.hold + special.turn).toBeGreaterThan(fill.hold + fill.turn)
  })
  it('a Holo or Special that fills a slot plays its sting, then the slot-fill sound', () => {
    expect(revealSounds({ fillsSlot: true }, 2)).toEqual({ now: 'reveal-2', then: 'slot-fill' })
    expect(revealSounds({ fillsSlot: true }, 3)).toEqual({ now: 'reveal-3', then: 'slot-fill' })
  })
  it('every sound of a reveal is shorter than 800 ms (A8)', () => {
    for (const level of [0, 1, 2, 3]) {
      for (const fillsSlot of [false, true]) {
        const s = revealSounds({ fillsSlot }, level)
        expect(sfxDuration(s.now)).toBeLessThan(MAX_SFX_MS)
        if (s.then) expect(sfxDuration(s.then)).toBeLessThan(MAX_SFX_MS)
      }
    }
  })
})
