import { describe, expect, it } from 'vitest'
import { closestTier, rewardText, type CardSet, type Goals, type PackCount } from '../data/economyApi.ts'
import { makeTestCard } from '../data/testCards.ts'
import type { TreatmentInfo } from '../data/types.ts'
import { checkInText, dailyHTML, freePackText, goalsHTML, masteryHTML, masteryShare, milestonesHTML, nextGoalText, setsHTML, sortSets } from './chase.ts'
import { emptyHTML } from './collection.ts'
import { bestPulls, heroText, mainAction, milestoneText, missingOne, rarestOwned, stillLowest } from './home.ts'
import { coinsTileText, milestoneBarWidth } from './stats.ts'
import { nextSteps } from '../packs/opening.ts'
import { packsBadge, packsTitle } from '../app/shell.ts'
import { saleQuote, versionsHeld, versionRows } from './inspect.ts'
import { milestoneReachedText } from '../ui/toast.ts'
import { ownedBreakdown, packLine } from './stats.ts'

const T: TreatmentInfo[] = [
  { id: 'normal', name: 'Normal', tier: 0, multiplier: 1, pack_weight: 780, pack_odds: 0.78 },
  { id: 'foil', name: 'Foil', tier: 1, multiplier: 1.5, pack_weight: 160, pack_odds: 0.16 },
  { id: 'holo', name: 'Holo', tier: 2, multiplier: 2.5, pack_weight: 50, pack_odds: 0.05 },
  { id: 'special', name: 'Special', tier: 3, multiplier: 3.5, pack_weight: 10, pack_odds: 0.01 },
]

const set = (name: string, size: number, tiers: [string, number, boolean, number, boolean][]): CardSet => ({
  tag_id: name.length, name, size,
  // a number stands for that many Standard packs (0 = no reward)
  tiers: tiers.map(([treatment, progress, complete, reward, claimed]) => ({ treatment, progress, complete, reward: packs(reward), claimed })),
})
const packs = (n: number): PackCount[] => (n > 0 ? [{ pack: 'standard', count: n }] : [])
const STD = packs(1)
const DLX: PackCount[] = [{ pack: 'deluxe', count: 1 }]

describe('today tiles', () => {
  it('offers the check-in and says what it unlocks (v0.9.0: no coins)', () => {
    expect(checkInText({ canCheckIn: true, streakDays: 0 })).toEqual({ title: 'Daily check-in: unlocks your free pack', note: 'Starts your streak' })
    expect(checkInText({ canCheckIn: true, streakDays: 4 }).note).toBe('Keeps your 4-day streak going')
  })
  it('after the check-in it points at tomorrow', () => {
    expect(checkInText({ canCheckIn: false, streakDays: 3 })).toEqual({ title: 'Day 3 of your streak', note: 'Come back tomorrow for the next free pack' })
  })
  it('names a reward as packs', () => {
    expect(rewardText(STD)).toBe('1 Standard pack')
    expect(rewardText([{ pack: 'deluxe', count: 2 }, { pack: 'premium', count: 1 }])).toBe('2 Deluxe packs + 1 Premium pack')
    expect(rewardText([])).toBe('')
  })
  it('free pack: ready, opened, or waiting for the check-in', () => {
    const base = { pack: 'standard', available: false, claimedToday: false, needsCheckin: false }
    expect(freePackText({ ...base, available: true })?.title).toBe('Your free pack is ready')
    expect(freePackText({ ...base, claimedToday: true })?.title).toBe('Free pack claimed')
    expect(freePackText({ ...base, needsCheckin: true })?.title).toBe('Free pack waiting')
    expect(freePackText({ ...base, pack: null })).toBeNull()
  })
})

describe('weekly goals', () => {
  it('locked goals say how many packs are left', () => {
    const g: Goals = { week: 'W1', locked: true, goals: [], unlock_after_packs: 30, packs_opened: 3 }
    expect(goalsHTML(g)).toContain('27 to go')
  })
  it('a finished goal gets a Claim button, a claimed one does not', () => {
    const g: Goals = { week: 'W1', locked: false, goals: [
      { id: 'open', title: 'Open 15 packs', kind: 'open', target: 15, reward: DLX, progress: 15, claimed: false },
      { id: 'tag', title: 'Apply 200 tags', kind: 'tag', target: 200, reward: DLX, progress: 200, claimed: true },
      { id: 'pull', title: '<b>Pull</b>', kind: 'pull', target: 12, reward: DLX, progress: 3, claimed: false },
    ] }
    const html = goalsHTML(g)
    expect(html).toContain('data-goal="open"')
    expect(html).toContain('Claim 1 Deluxe pack')
    expect(html).not.toContain('data-goal="tag"')
    expect(html).not.toContain('<b>Pull</b>')
  })
})

describe('sets', () => {
  const pets = set('pets', 5, [['foil', 5, true, 120, false], ['holo', 1, false, 200, false]])
  const trips = set('trips', 10, [['foil', 9, false, 150, false]])
  const done = set('done', 5, [['foil', 5, true, 0, true]])
  it('puts the set closest to a reward first and finished sets last', () => {
    expect(sortSets([done, trips, pets]).map((s) => s.name)).toEqual(['pets', 'trips', 'done'])
  })
  it('finds the unclaimed tier closest to completion', () => {
    expect(closestTier([done, trips])?.set.name).toBe('trips')
    expect(closestTier([done])).toBeNull()
  })
  it('explains sets when there are none', () => {
    expect(setsHTML([])).toContain('5 or more cards')
  })
  it('shows a Claim button only for a complete, unclaimed tier', () => {
    const html = setsHTML([pets, done])
    expect(html.match(/data-set=/g)).toHaveLength(1)
    expect(html).toContain('4 to go')
  })
})

describe('home and stats numbers', () => {
  const cards = [
    makeTestCard({ id: 1, treatment: 'normal', copies: 0 }), // never pulled: has no treatment yet
    makeTestCard({ id: 2, treatment: 'foil', copies: 1 }),
    makeTestCard({ id: 3, treatment: 'holo', copies: 2 }),
    makeTestCard({ id: 4, treatment: 'normal', copies: 1 }),
  ]
  it('the rarest pull ignores cards never pulled', () => {
    expect(rarestOwned(cards, T)?.id).toBe('holo')
    expect(rarestOwned([cards[0]], T)).toBeNull()
  })
  it('best pulls are owned cards, rarest first', () => {
    expect(bestPulls(cards, T, 2).map((c) => c.id)).toEqual([3, 2])
  })
  it('counts owned cards per treatment and the ones not pulled', () => {
    const b = ownedBreakdown(cards, T)
    expect(b.rows.map((r) => r.n)).toEqual([1, 1, 1, 0])
    expect(b.unpulled).toBe(1)
  })
  it('says how far the next milestone is', () => {
    expect(milestoneText(0.021, { at: 0.1, reward: DLX })).toBe('7.9% to go until 10% · 1 Deluxe pack')
    expect(milestoneText(0.001, { at: 0.1, reward: DLX })).toBe('9.9% to go until 10% · 1 Deluxe pack')
    expect(milestoneText(0.05, { at: 0.1, reward: DLX })).toBe('5% to go until 10% · 1 Deluxe pack')
    expect(milestoneText(0.099, { at: 0.1, reward: DLX })).toBe('0.1% to go until 10% · 1 Deluxe pack')
    expect(milestoneText(1, null)).toBeNull()
  })
  it('sums up a pack in one line', () => {
    const line = packLine({ id: 1, pack: 'standard', at: '', cost: 0, free: true, cards: 7, best: 'holo', near_misses: 0, new: 5, better: 0, upgraded: 2, duplicate: 0 },
      () => 'Standard Pack', (id) => id.toUpperCase())
    expect(line).toBe('Standard Pack (free): 5 new, 2 upgraded · best pull: HOLO')
  })
})

describe('milestones and ladder', () => {
  // v0.9.0 (D4): the soft-pity line is gone. A milestone is reached, then claimed.
  it('a reached milestone says what waits to be claimed', () => {
    expect(milestoneReachedText({ at: 0.05, reward: STD })).toBe('Milestone 5% reached: 1 Standard pack to claim')
    expect(milestoneReachedText({ at: 0.01, reward: [] })).toBe('Milestone 1% reached')
  })
  it('the claim button shows only while a milestone waits', () => {
    expect(milestonesHTML(undefined)).toBe('')
    expect(milestonesHTML({ claimable: [], claimable_packs: [], next: null })).toBe('')
    const html = milestonesHTML({ claimable: [{ at: 0.01, reward: STD }, { at: 0.02, reward: STD }], claimable_packs: packs(2), next: null })
    expect(html).toContain('data-milestones')
    expect(html).toContain('2 milestones reached')
    expect(html).toContain('Claim 2 Standard packs')
  })
  it('"Your versions" lists all four treatments, held only while a copy is held (v0.7.1, L5)', () => {
    const held = (card: ReturnType<typeof makeTestCard>) => versionRows(card, T).map((r) => r.copies)
    expect(held(makeTestCard({ treatment: 'foil', copies: 3, copiesByTreatment: { normal: 2, foil: 1 } }))).toEqual([2, 1, 0, 0])
    expect(held(makeTestCard({ treatment: 'normal', copies: 0 }))).toEqual([0, 0, 0, 0])
    // A Normal and a Special copy: Foil and Holo are not held (v0.7.0 ticked all four).
    const gap = makeTestCard({ treatment: 'special', copies: 2, copiesByTreatment: { normal: 1, special: 1 } })
    expect(held(gap)).toEqual([1, 0, 0, 1])
    expect(versionsHeld(gap, T)).toEqual({ held: 1, of: 3 })
  })
})

describe('empty collection results', () => {
  it('always offers the action that fills the list', () => {
    expect(emptyHTML('holo', false)).toContain('href="#/packs"')
    expect(emptyHTML('untagged', false)).toContain('Every card here is tagged')
    expect(emptyHTML('all', true)).toContain('href="#/organize"')
    expect(emptyHTML('all', false)).toContain('href="#/packs"') // an empty binder: every card comes out of a pack
  })
})

describe('the main action on Home', () => {
  const packs = [{ id: 'standard', name: 'Standard', cost: 100 }, { id: 'premium', name: 'Premium', cost: 480 }]
  it('opens a sealed pack when you own one', () => {
    const a = mainAction({ inventory: { standard: 1, premium: 3 }, packs, coins: 0, untaggedOwned: 4 })
    expect(a).toEqual({ kind: 'open', label: 'Open a pack · 4 sealed', packId: 'premium' })
  })
  it('else buys the cheapest pack you can afford', () => {
    // The shop opens with that pack highlighted.
    expect(mainAction({ inventory: {}, packs, coins: 120, untaggedOwned: 4, missing: 9 })).toMatchObject({ kind: 'buy', href: '#/packs?pack=standard', packId: 'standard' })
    // out of packs, coins and untagged cards: there is still something to do
    expect(mainAction({ inventory: {}, packs, coins: 0, untaggedOwned: 0, tagMore: 7, spares: 3 })).toMatchObject({ kind: 'tag-more', href: '#/organize' })
    expect(mainAction({ inventory: {}, packs, coins: 0, untaggedOwned: 0, tagMore: 0, spares: 3 })).toMatchObject({ kind: 'sell', href: '#/market' })
    expect(mainAction({ inventory: {}, packs, coins: 0, untaggedOwned: 0 })).toMatchObject({ kind: 'earn' })
  })
  it('else, with missing versions, goes to the Missing-versions list (rule 3 of the spec)', () => {
    expect(mainAction({ inventory: {}, packs, coins: 20, untaggedOwned: 4, missing: 12 })).toEqual({
      kind: 'fill', label: 'Fill versions · 12 cards', href: '#/collection?filter=missing&from=home',
    })
    expect(mainAction({ inventory: {}, packs, coins: 20, untaggedOwned: 0, missing: 1 }).label).toBe('Fill versions · 1 card')
    // A sealed pack and an affordable pack both come first.
    expect(mainAction({ inventory: { standard: 1 }, packs, coins: 0, untaggedOwned: 0, missing: 12 }).kind).toBe('open')
  })
  it('"Missing one version" lists the cards one version short, the most valuable first', () => {
    const cards = [
      makeTestCard({ id: 1, copies: 3, value: 10, copiesByTreatment: { normal: 1, foil: 1, holo: 1 } }),
      makeTestCard({ id: 2, copies: 2, value: 90, copiesByTreatment: { foil: 1, special: 1 } }),
      makeTestCard({ id: 3, copies: 1, value: 500, copiesByTreatment: { foil: 1 } }), // two missing
      makeTestCard({ id: 4, copies: 0, value: 900, copiesByTreatment: {} }), // not pulled
      makeTestCard({ id: 5, copies: 3, value: 70, copiesByTreatment: { foil: 1, holo: 1, special: 1 } }), // none missing
    ]
    expect(missingOne(cards).map((x) => [x.card.id, x.missing])).toEqual([[2, 'holo'], [1, 'special']])
    expect(missingOne(cards, 1)).toHaveLength(1)
  })
  it('else sends you to tag the cards you pulled', () => {
    expect(mainAction({ inventory: {}, packs, coins: 20, untaggedOwned: 4 })).toMatchObject({ kind: 'tag', href: '#/organize', label: 'Tag 4 cards to earn coins' })
  })
  it('else shows the ways to earn', () => {
    expect(mainAction({ inventory: {}, packs, coins: 20, untaggedOwned: 0 }).kind).toBe('earn')
  })
})

describe('v0.6.0: goals that keep going', () => {
  const goal = (o: object) => ({ id: 'g', title: 'Fuse 1 card', kind: 'fuse', target: 1, reward: STD, progress: 0, claimed: false, ...o })
  it("today's goals show progress, a claim button and when new ones come", () => {
    const now = Date.parse('2026-11-14T20:00:00Z')
    const html = dailyHTML({ key: 'd', locked: false, ends_at: '2026-11-14T23:00:00Z', goals: [goal({ progress: 1 }), goal({ id: 'h', title: 'Open 2 packs', target: 2 })] }, now)
    expect(html).toContain('data-chase="daily:g"')
    expect(html).toContain('Claim 1 Standard pack')
    expect(html).toContain('0/2')
    expect(html).toContain('New goals in 3 h 00 min')
    const done = dailyHTML({ key: 'd', locked: false, ends_at: '2026-11-14T23:00:00Z', goals: [goal({ progress: 1, claimed: true })] }, now)
    expect(done).toContain('All done for today.')
  })
  it('daily goals start after a few packs', () => {
    expect(dailyHTML({ key: 'd', locked: true, unlock_after_packs: 5, packs_opened: 2, goals: [] })).toContain('3 to go')
  })
  it('the collector level shows the way to the next one, or a claim', () => {
    const m = { level: 7, points: 140, level_at: 120, next_at: 160, next_reward: STD, claimable: [], claimable_packs: [] }
    expect(masteryShare(m)).toBe(0.5)
    expect(masteryHTML(m)).toContain('20 points to level 8')
    expect(masteryHTML(m)).toContain('1 Standard pack')
    expect(masteryHTML({ ...m, claimable: [{ level: 7, reward: STD }], claimable_packs: STD })).toContain('Claim 1 Standard pack')
    expect(masteryShare({ points: 5, level_at: 10, next_at: 10 })).toBe(0)
  })
  it('names the one next goal', () => {
    expect(nextGoalText(null)).toBeNull()
    expect(nextGoalText({ kind: 'daily', title: 'Fuse 1 card', progress: 0, target: 1, reward: STD })).toEqual({ big: '0/1', small: 'Fuse 1 card · 1 Standard pack' })
    expect(nextGoalText({ kind: 'weekly', title: 'Sell 15 copies', progress: 15, target: 15, reward: DLX })?.big).toBe('Claim it')
    expect(nextGoalText({ kind: 'milestone', title: 'Collection 35% complete', progress: 33.4, target: 35, reward: DLX })?.big).toBe('33.4% of 35%')
    expect(nextGoalText({ kind: 'mastery', title: 'Collector level 8', progress: 140, target: 160, reward: STD, claimable: true })?.big).toBe('Claim it')
  })
})

describe('v0.6.0: what is still missing', () => {
  const treatments = [{ id: 'normal', name: 'Normal', tier: 0 }, { id: 'foil', name: 'Foil', tier: 1 }] as never
  it('counts the cards still at the lowest treatment', () => {
    const cards = [makeTestCard({ id: 1, copies: 1, treatment: 'normal' }), makeTestCard({ id: 2, copies: 2, treatment: 'foil' }), makeTestCard({ id: 3, copies: 0, treatment: 'normal' })]
    expect(stillLowest(cards, treatments)).toEqual({ count: 1, name: 'Normal' })
  })
  it('the Home line names what is left, also in a finished library', () => {
    const base = { owned: 40, total: 40, untaggedOwned: 0, complete: 0.33, lowest: { count: 31, name: 'Normal' }, short: 0, packName: 'Standard Pack' }
    expect(heroText(base)).toBe('31 of your cards are still Normal: merge copies or pull a rarer one. You hold 33% of all versions.')
    expect(heroText({ ...base, total: 60, untaggedOwned: 4, short: 82, versions: 40, versionsTotal: 120 }))
      .toBe('20 still to pull. 4 of your cards need tags, and each tag earns coins. 82 more coins for a Standard Pack. You hold 40 of 120 versions (33%).')
    // The words the spec keeps out of primary UI (9.2).
    expect(heroText(base)).not.toMatch(/complete|finished|Every card is pulled/)
    expect(heroText({ ...base, owned: 0 })).toMatch(/waiting in a pack/)
  })
})

describe('v0.6.0: from a pack to the next action', () => {
  const name = (id: string) => id.toUpperCase()
  const next = (id: string) => (id === 'normal' ? 'foil' : null)
  const pull = (card: object) => ({ card: makeTestCard(card), isNew: false, nearMiss: false, outcome: 'duplicate', coins: 0, value: 1 }) as never
  it('offers a merge for a card that reached enough copies, then a wanted card', () => {
    const steps = nextSteps([
      pull({ id: 1, title: 'Sea', fuseFrom: 'normal' }), pull({ id: 1, title: 'Sea', fuseFrom: 'normal' }),
      pull({ id: 2, title: 'Sun', wanted: true }), pull({ id: 3, title: 'Top', fuseFrom: 'special' }),
    ], 3, name, next)
    expect(steps.map((s) => [s.kind, s.id])).toEqual([['fuse', 1], ['wanted', 2]])
    expect(steps[0].label).toBe('Merge 3 NORMAL → FOIL: Sea')
  })
  it('shows at most three', () => {
    const many = [1, 2, 3, 4, 5].map((id) => pull({ id, title: `C${id}`, fuseFrom: 'normal' }))
    expect(nextSteps(many, 3, name, next)).toHaveLength(3)
    expect(nextSteps([pull({ id: 9 })], 3, name, next)).toEqual([])
  })
  it('the Packs button counts sealed packs, or says the free pack is ready', () => {
    expect(packsBadge(3, true)).toBe('3')
    expect(packsBadge(120, false)).toBe('99+')
    expect(packsBadge(0, true)).toBe('free')
    expect(packsBadge(0, false)).toBe('')
    expect(packsTitle(2, 5, false)).toBe('2 sealed packs to open · you can afford ×5')
    expect(packsTitle(0, 0, true)).toBe('No sealed packs · your free pack is ready · not enough coins for a pack')
  })
})

describe('v0.7.1: Home and Stats', () => {
  const packs = [{ id: 'standard', name: 'Standard Pack', cost: 100 }]

  it('words the next milestone in versions when the library shrank (L3)', () => {
    // 3 of 900 versions against the 300 cards the library once had; 1 card is left, shown as 100% complete.
    const next = { at: 0.01, reward: STD, progress: 3 / 900, versionsNeeded: 6, cards: 300 }
    expect(milestoneText(1, next, 1)).toBe('6 more versions until the 1% milestone (counted against the 300 cards your library had at its largest) · 1 Standard pack')
    expect(milestoneText(1, { ...next, versionsNeeded: 1 }, 1)).toMatch(/^1 more version until/)
    // A library that never shrank keeps the percentage, measured on the milestones' own progress.
    expect(milestoneText(0.021, { at: 0.1, reward: DLX, progress: 0.021, versionsNeeded: 24, cards: 100 }, 100)).toBe('7.9% to go until 10% · 1 Deluxe pack')
    expect(milestoneBarWidth(1, next)).toBeCloseTo(33.33, 1)
    expect(milestoneBarWidth(0.05, { at: 0.1 })).toBe(50)
    expect(milestoneBarWidth(0.5, null)).toBe(100)
  })

  it('with nothing left to earn, the main action is to sell a card, and the hero names tomorrow (D1)', () => {
    const broke = { inventory: {}, packs, coins: 47, untaggedOwned: 0, tagMore: 0, spares: 0 }
    const a = mainAction({ ...broke, cards: 11, worth: 866 })
    expect(a).toMatchObject({ kind: 'sell-card', href: '#/market' })
    expect(a.label).toBe(`Sell a card on the market · yours are worth 🪙 ${(866).toLocaleString()}`)
    // No card at all: the help article, never a page that explains nothing.
    const none = mainAction({ ...broke, cards: 0, worth: 0 })
    expect(none.kind).toBe('earn')
    expect(none.href).toBeUndefined()
    const base = { owned: 12, total: 12, untaggedOwned: 0, complete: 0.33, lowest: { count: 0, name: 'Normal' }, short: 53, packName: 'Standard Pack' }
    expect(heroText({ ...base, tomorrow: true })).toContain("53 more coins for a Standard Pack. Tomorrow's check-in brings a free pack.")
    expect(heroText({ ...base, short: 0, tomorrow: true })).not.toContain('Tomorrow')
    expect(heroText(base)).not.toContain('Tomorrow')
  })

  it('the Coins tile text follows the balance (B9), and a pack line names new versions (L6)', () => {
    expect(coinsTileText(21186)).toBe(`🪙 ${(21186).toLocaleString()}`)
    const p = { id: 1, pack: 'standard', at: '', cost: 100, free: false, cards: 5, best: 'foil', near_misses: 0, new: 1, better: 0, version: 2, upgraded: 0, duplicate: 2 }
    expect(packLine(p, () => 'Standard Pack', (id) => id)).toBe('Standard Pack: 1 new, 2 new versions, 2 duplicates · best pull: foil')
  })

  it('quotes a sale of several copies with the falling price (B4)', () => {
    const card = makeTestCard({ copies: 16, copiesByTreatment: { normal: 16 }, sellPrices: { normal: 7 }, sellExtras: { normal: 83 } })
    expect(saleQuote(card, 'normal', 1)).toBe(7)
    expect(saleQuote(card, 'normal', 15)).toBe(83) // v0.7.0 said 15 × 7 = 105
    // v0.8.0: the server's quote per count wins.
    expect(saleQuote({ ...card, saleQuotes: { normal: [7, 14, 21] } }, 'normal', 3)).toBe(21)
    expect(saleQuote({ ...card, sellExtras: {} }, 'normal', 15)).toBe(105) // an older server: the plain product
  })
})
