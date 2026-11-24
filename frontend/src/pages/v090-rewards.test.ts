/** v0.9.0: rewards are packs everywhere (D1), the Home side bar (P1), and what a scan pays. */
import { describe, expect, it } from 'vitest'
import { rewardText } from '../data/economyApi.ts'
import { toursRewardText } from '../data/onboardingApi.ts'
import { scanCoinsText, scanSummary } from '../data/scan.ts'
import type { ScanPreview, ScanStatus } from '../data/api.ts'
import { scanAddedText, scanQuestion } from '../ui/scanFlow.ts'
import { milestonesHTML } from './chase.ts'
import { sealedLine } from './home.ts'

const STD = [{ pack: 'standard', count: 1 }]

describe('the side bar of Home (P1)', () => {
  it('its first row counts the sealed packs', () => {
    expect(sealedLine(0)).toBe('No sealed packs')
    expect(sealedLine(1)).toBe('1 sealed pack')
    expect(sealedLine(4)).toBe('4 sealed packs')
    expect(sealedLine(1234)).toBe(`${(1234).toLocaleString()} sealed packs`)
  })
  it('the milestone row stays when nothing waits: it names the next one, so a claim does not collapse the panel', () => {
    const next = milestonesHTML({ claimable: [], claimable_packs: [], next: { at: 0.02, reward: STD } })
    expect(next).toContain('Next milestone')
    expect(next).toContain('2%')
    expect(next).toContain('1 Standard pack')
    expect(next).not.toContain('<button')
    const waiting = milestonesHTML({ claimable: [{ at: 0.01, reward: STD }], claimable_packs: STD, next: { at: 0.02, reward: STD } })
    expect(waiting).toContain('data-milestones')
    expect(waiting).toContain('Claim 1 Standard pack')
    expect(milestonesHTML({ claimable: [], claimable_packs: [], next: null })).toBe('') // every milestone is reached and claimed
  })
})

describe('every reward is named in packs, never in coins (D1)', () => {
  it('reward text', () => {
    expect(rewardText(STD)).toBe('1 Standard pack')
    expect(rewardText([{ pack: 'deluxe', count: 2 }, { pack: 'premium', count: 1 }])).toBe('2 Deluxe packs + 1 Premium pack')
    expect(rewardText([])).toBe('')
    expect(rewardText(STD)).not.toMatch(/coin/i)
  })
  it('the tours pay once, when all are taken, and say how far that is', () => {
    const tours = ['welcome', 'organize', 'packs', 'card-panel', 'market']
    expect(toursRewardText({ tours, completed: 3, reward: STD, claimed: false })).toBe('3 of 5 tours taken · 1 Standard pack when all are taken')
    expect(toursRewardText({ tours, completed: 0, reward: STD, claimed: false })).toBe('0 of 5 tours taken · 1 Standard pack when all are taken')
    expect(toursRewardText({ tours, completed: 9, reward: STD, claimed: false })).toMatch(/^5 of 5/)
    expect(toursRewardText({ tours, completed: 5, reward: STD, claimed: true })).toBe('All 5 tours taken · 1 Standard pack paid')
    expect(toursRewardText({ tours, completed: 2, reward: [], claimed: false })).toBe('') // the tours pay nothing
    expect(toursRewardText(undefined)).toBe('') // an older server
    expect(toursRewardText({ tours, completed: 3, reward: STD, claimed: false })).not.toMatch(/coin/i)
  })
})

describe('what a scan pays, and why it paid less (D1)', () => {
  it('names the coins', () => {
    expect(scanCoinsText({ coins: 45, coins_full: 45, coins_reason: null })).toBe('+45 coins')
    expect(scanCoinsText({ coins: 1, coins_full: 1 })).toBe('+1 coin')
    expect(scanCoinsText({ coins: 1500, coins_full: 1500 })).toBe(`+${(1500).toLocaleString()} coins`)
  })
  it('the first import pays nothing, and says so', () => {
    expect(scanCoinsText({ coins: 0, coins_full: 1200, coins_reason: 'first_import' })).toBe('0 coins: the first import pays nothing')
  })
  it('the daily limit, with how much of it is used when the report says', () => {
    expect(scanCoinsText({ coins: 200, coins_full: 360, coins_reason: 'daily_limit', ingest_day: { paid: 200, cap: 200 } }))
      .toBe('+200 coins (daily limit reached: 200 of 200)')
    expect(scanCoinsText({ coins: 0, coins_full: 90, coins_reason: 'daily_limit', ingest_day: { paid: 200, cap: 200 } }))
      .toBe('0 coins (daily limit reached: 200 of 200)')
    expect(scanCoinsText({ coins: 200, coins_full: 360, coins_reason: 'daily_limit' })).toBe('+200 coins (daily limit reached)') // the preview has no day
  })
  it('says nothing when the scan found nothing that pays', () => {
    expect(scanCoinsText({ coins: 0, coins_full: 0, coins_reason: null })).toBe('')
    expect(scanCoinsText(null)).toBe('')
    expect(scanCoinsText({})).toBe('')
  })
  it('is part of the scan report and of the question before a scan', () => {
    const report = { new: 18, new_media: 18, new_slots: 54, coins: 200, coins_full: 360, coins_reason: 'daily_limit', ingest_day: { paid: 200, cap: 200 } }
    expect(scanAddedText(report)).toBe('+18 media · +54 version slots · +200 coins (daily limit reached: 200 of 200)')
    expect(scanAddedText({ new_media: 60, new_slots: 180, coins: 0, coins_full: 1200, coins_reason: 'first_import' }))
      .toBe('+60 media · +180 version slots · 0 coins: the first import pays nothing')
    const st = { state: 'idle', done: 18, total: 18, last_report: report } as unknown as ScanStatus
    expect(scanSummary(st)).toBe('18 new cards · +200 coins (daily limit reached: 200 of 200)')
    const preview: ScanPreview = {
      new_media: 18, restored_media: 0, missing_media: 0, new_slots: 54, slots_filled: 4, slots_total: 180, slots_filled_after: 4,
      slots_total_after: 234, completeness_now: 0.022, completeness_after: 0.017, coins: 200, coins_full: 360, coins_reason: 'daily_limit',
    }
    const q = scanQuestion(preview)!
    expect(q.rows?.find((r) => r.label === 'Coins for the new files')?.value).toBe('+200 coins (daily limit reached)')
    expect(scanQuestion({ ...preview, coins: 0, coins_full: 0, coins_reason: null })!.rows?.some((r) => r.label === 'Coins for the new files')).toBe(false)
  })
})
