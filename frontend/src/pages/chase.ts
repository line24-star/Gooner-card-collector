/**
 * The always-open targets shown on Home and Stats: today's check-in and free pack, weekly goals
 * and category sets. Each panel renders itself into an element and re-renders after a claim.
 */

import './chase.css'
import { esc } from '../cards/card.ts'
import { visualFor } from '../cards/treatments.ts'
import { ApiError } from '../data/api.ts'
import {
  checkIn, claimChase, claimGoal, claimMastery, claimMilestones, claimSet, fetchChase, fetchGoals, fetchSets, rewardText,
  type CardSet, type Chase, type ClaimResult, type Goal, type GoalPeriod, type Goals, type Mastery, type MilestonesStatus,
  type NextGoal,
} from '../data/economyApi.ts'
import { countdownText } from '../ui/countdown.ts'
import { claimFreePack, getState, refreshWallet } from '../data/store.ts'
import { play } from '../fx/sound.ts'
import { toast, toastMilestones } from '../ui/toast.ts'

/** After a claim: the packs are in the inventory (the reply carries it), and the toast names them. */
function claimed(what: string, r: ClaimResult): void {
  getState().wallet.inventory = r.inventory
  play('coin')
  const packs = rewardText(r.packs)
  toast(packs ? `${what}: +${packs}` : what, { kind: 'good', action: { label: 'Open a pack', href: '#/packs' } })
}

const pctWidth = (done: number, of: number) => `${of > 0 ? Math.min(100, Math.round((done / of) * 100)) : 0}%`

// ---------------------------------------------------------------- today: check-in and free pack

/** What the check-in tile says, from the wallet alone. */
export function checkInText(w: { canCheckIn: boolean; streakDays: number }): { title: string; note: string } {
  if (w.canCheckIn) {
    return { title: 'Daily check-in: unlocks your free pack', note: w.streakDays > 0 ? `Keeps your ${w.streakDays}-day streak going` : 'Starts your streak' }
  }
  return { title: `Day ${w.streakDays} of your streak`, note: 'Come back tomorrow for the next free pack' }
}

export function freePackText(f: { pack: string | null; available: boolean; claimedToday: boolean; needsCheckin: boolean }): { title: string; note: string } | null {
  if (!f.pack) return null
  if (f.available) return { title: 'Your free pack is ready', note: 'One free pack every day. It never raises prices.' }
  if (f.claimedToday) return { title: 'Free pack claimed', note: 'The next one is ready tomorrow.' }
  return { title: 'Free pack waiting', note: 'Check in first, then claim it.' }
}

export function renderToday(el: HTMLElement, onChange?: () => void): void {
  const draw = () => {
    const w = getState().wallet
    const ci = checkInText(w)
    const fp = freePackText(w.freePack)
    el.innerHTML = `
      <section class="today" data-tour="today" aria-label="Today">
        <div class="today-tile ${w.canCheckIn ? 'is-ready' : ''}">
          <span class="today-icon" aria-hidden="true">🔥</span>
          <div class="today-text"><b>${esc(ci.title)}</b><span class="muted">${esc(ci.note)}</span></div>
          ${w.canCheckIn ? '<button class="btn" type="button" data-act="checkin">Claim</button>' : ''}
        </div>
        ${fp ? `<div class="today-tile ${w.freePack.available ? 'is-ready' : ''}">
          <span class="today-icon" aria-hidden="true">🎁</span>
          <div class="today-text"><b>${esc(fp.title)}</b><span class="muted">${esc(fp.note)}</span></div>
          ${w.freePack.available ? '<button class="btn" type="button" data-act="free">Claim</button>' : ''}
        </div>` : ''}
      </section>`
  }
  el.onclick = async (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-act]')
    if (!btn || btn.disabled) return
    if (btn.dataset.act === 'checkin') {
      btn.disabled = true
      try {
        const r = await checkIn()
        play('coin')
        toast(`Day ${r.streak_days} of your streak: your free pack is ready`, { kind: 'good' })
        toastMilestones(r.milestones)
      } catch (err) {
        toast(err instanceof Error ? err.message : 'Could not check in. Try again.', { kind: 'warn' })
      }
      await refreshWallet().catch(() => {})
      draw()
      onChange?.()
    } else if (btn.dataset.act === 'free') {
      btn.disabled = true
      try {
        await claimFreePack()
        play('coin')
        toast('Your free pack is in your packs, sealed', { kind: 'good' })
      } catch (err) {
        toast(err instanceof Error ? err.message : 'Could not claim the free pack.', { kind: 'warn' })
      }
      draw()
      onChange?.()
    }
  }
  draw()
}

// ---------------------------------------------------------------- weekly goals

function goalRow(g: Goal, claimAttr = `data-goal="${esc(g.id)}"`): string {
  const done = g.progress >= g.target
  const action = g.claimed
    ? '<span class="muted chase-action">Claimed</span>'
    : done
      ? `<button class="btn chase-action" type="button" ${claimAttr}>Claim ${esc(rewardText(g.reward))}</button>`
      : `<span class="chase-reward chase-action">${esc(rewardText(g.reward))}</span>`
  return `<li class="chase-row ${g.claimed ? 'is-claimed' : ''}">
    <span class="chase-title">${g.claimed ? '✓ ' : ''}${esc(g.title)}</span>
    <span class="chase-bar"><span class="progress"><span class="progress-fill" style="width:${pctWidth(g.progress, g.target)}"></span></span>${g.progress}/${g.target}</span>
    ${action}
  </li>`
}

export function goalsHTML(data: Goals): string {
  if (data.locked) {
    const left = Math.max(0, (data.unlock_after_packs ?? 0) - (data.packs_opened ?? 0))
    return `<p class="muted">Weekly goals unlock after ${data.unlock_after_packs} packs. <b>${left} to go.</b></p>
      <div class="progress"><div class="progress-fill" style="width:${pctWidth(data.packs_opened ?? 0, data.unlock_after_packs ?? 1)}"></div></div>`
  }
  if (!data.goals.length) return '<p class="muted">No goals this week.</p>'
  return `<ul class="chase-list">${data.goals.map((g) => goalRow(g)).join('')}</ul>
    <p class="muted chase-foot">Three new goals every Monday.</p>`
}

export async function renderGoals(el: HTMLElement): Promise<void> {
  let data: Goals
  try { data = await fetchGoals() } catch { el.innerHTML = '<p class="muted">Weekly goals could not be loaded.</p>'; return }
  el.innerHTML = goalsHTML(data)
  el.onclick = async (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-goal]')
    if (!btn || btn.disabled) return
    btn.disabled = true
    try {
      claimed('Weekly goal done', await claimGoal(btn.dataset.goal!))
      await refreshWallet().catch(() => {})
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 409)) { btn.disabled = false; return }
    }
    await renderGoals(el)
  }
}

// ---------------------------------------------------------------- today's goals and the collector level (v0.6.0)

/** Today's goals: they need no untagged cards, so there is something to do in a finished library too. */
export function dailyHTML(d: GoalPeriod, now = Date.now()): string {
  if (d.locked) {
    const left = Math.max(0, (d.unlock_after_packs ?? 0) - (d.packs_opened ?? 0))
    return `<p class="muted">Daily goals start after ${d.unlock_after_packs} packs. <b>${left} to go.</b></p>`
  }
  if (!d.goals.length) return '<p class="muted">No goals today.</p>'
  const open = d.goals.filter((g) => !g.claimed).length
  return `<ul class="chase-list">${d.goals.map((g) => goalRow(g, `data-chase="daily:${esc(g.id)}"`)).join('')}</ul>
    <p class="muted chase-foot">${open ? '' : 'All done for today. '}New goals in ${countdownText(d.ends_at ?? null, now)}.</p>`
}

/** How far the collector level is toward the next one, 0 to 1. */
export function masteryShare(m: Pick<Mastery, 'points' | 'level_at' | 'next_at'>): number {
  const span = m.next_at - m.level_at
  return span > 0 ? Math.max(0, Math.min(1, (m.points - m.level_at) / span)) : 0
}

export function masteryHTML(m: Mastery): string {
  const left = Math.max(0, m.next_at - m.points)
  return `<div class="mastery" title="Foil, Holo and Special cards give points (rarer gives more), and so does every merge. Normal cards give none. Each new level pays a pack.">
    <span class="mastery-level">Collector level <b>${m.level}</b></span>
    <span class="chase-bar"><span class="progress"><span class="progress-fill" style="width:${masteryShare(m) * 100}%"></span></span>${m.points.toLocaleString()}/${m.next_at.toLocaleString()}</span>
    ${m.claimable.length > 0
      ? `<button class="btn chase-action" type="button" data-mastery>Claim ${esc(rewardText(m.claimable_packs))}</button>`
      : `<span class="chase-reward chase-action">${left.toLocaleString()} points to level ${m.level + 1} · ${esc(rewardText(m.next_reward))}</span>`}
  </div>`
}

/** Milestones that are reached and wait to be claimed (v0.9.0): one line and one button, or nothing. */
export function milestonesHTML(ms: MilestonesStatus | undefined): string {
  if (!ms) return ''
  if (!ms.claimable.length) {
    // Nothing waits: the row stays, naming the next one, so the panel keeps its height after a claim.
    if (!ms.next) return ''
    const pct = (ms.next.at * 100).toLocaleString('en-US', { maximumFractionDigits: 1 })
    return `<div class="mastery mastery-next">
      <span class="mastery-level">Next milestone <b>${pct}%</b></span>
      <span class="chase-reward chase-action">${esc(rewardText(ms.next.reward))}</span>
    </div>`
  }
  const n = ms.claimable.length
  return `<div class="mastery">
    <span class="mastery-level">${n === 1 ? 'Milestone reached' : `${n} milestones reached`}</span>
    <button class="btn chase-action" type="button" data-milestones>Claim ${esc(rewardText(ms.claimable_packs))}</button>
  </div>`
}

/** The tile for "the next goal": a big number and one line that says what and what it pays. */
export function nextGoalText(n: NextGoal | null): { big: string; small: string } | null {
  if (!n) return null
  const done = n.claimable ?? n.progress >= n.target
  const num = (v: number) => v.toLocaleString('en-US', { maximumFractionDigits: 1 })
  // A completeness milestone counts in percent; everything else in whole steps.
  const big = done ? 'Claim it' : n.kind === 'milestone' ? `${num(n.progress)}% of ${num(n.target)}%` : `${num(n.progress)}/${num(n.target)}`
  const packs = rewardText(n.reward)
  return { big, small: packs ? `${n.title} · ${packs}` : n.title }
}

/** Today's goals with the collector level under them. Returns what was loaded (for the "next goal" tile). */
export async function renderDaily(el: HTMLElement, onChange?: (c: Chase) => void): Promise<Chase | null> {
  let data: Chase
  try { data = await fetchChase() } catch { el.innerHTML = ''; return null }
  el.innerHTML = `<section class="panel chase-daily" data-tour="daily-goals">
    <div class="chase-head"><h3>Today's goals</h3><a href="#/stats" class="muted">Weekly goals and sets</a></div>
    ${dailyHTML(data.daily)}
    ${masteryHTML(data.mastery)}
    ${milestonesHTML(data.milestones)}
  </section>`
  el.onclick = async (e) => {
    const t = e.target as HTMLElement
    const goal = t.closest<HTMLButtonElement>('[data-chase]')
    const level = t.closest<HTMLButtonElement>('[data-mastery]')
    const stones = t.closest<HTMLButtonElement>('[data-milestones]')
    const btn = goal ?? level ?? stones
    if (!btn || btn.disabled) return
    btn.disabled = true
    try {
      if (goal) {
        const [scope, id] = goal.dataset.chase!.split(':') as ['daily' | 'weekly', string]
        claimed('Goal done', await claimChase(scope, id))
      } else if (level) {
        const r = await claimMastery()
        claimed(`Collector level ${Math.max(...r.levels)}`, r)
      } else {
        const r = await claimMilestones()
        claimed(r.milestones.length === 1 ? 'Milestone claimed' : `${r.milestones.length} milestones claimed`, r)
      }
      await refreshWallet().catch(() => {})
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 409)) { btn.disabled = false; return }
    }
    const fresh = await renderDaily(el, onChange)
    if (fresh) onChange?.(fresh)
  }
  return data
}

// ---------------------------------------------------------------- category sets

function setHTML(s: CardSet): string {
  return `<li class="set">
    <div class="set-head"><a href="#/collection?tag=${s.tag_id}"><b>${esc(s.name)}</b></a><span class="muted">${s.size} cards</span></div>
    <div class="set-tiers">
      ${s.tiers.map((t) => {
        const v = visualFor(t.treatment)
        const left = s.size - t.progress
        const action = t.claimed
          ? `<span class="muted">${t.complete ? 'Claimed' : 'Already counted'}</span>`
          : t.complete
            ? `<button class="btn" type="button" data-set="${s.tag_id}" data-treatment="${esc(t.treatment)}">Claim ${esc(rewardText(t.reward))}</button>`
            : `<span class="muted">${left} to go · ${esc(rewardText(t.reward))}</span>`
        return `<div class="set-tier" style="--rarity:${v.color}">
          <span class="set-tier-name">${esc(v.label)}</span>
          <span class="rarity-bar"><span style="width:${pctWidth(t.progress, s.size)}"></span></span>
          <span class="set-tier-n">${t.progress}/${s.size}</span>
          ${action}
        </div>`
      }).join('')}
    </div>
  </li>`
}

/** Sets closest to a reward first, finished-and-claimed ones last. */
export function sortSets(sets: CardSet[]): CardSet[] {
  const score = (s: CardSet) => {
    const open = s.tiers.filter((t) => !t.claimed)
    if (!open.length) return -1
    return Math.max(...open.map((t) => (t.complete ? 2 : t.progress / s.size)))
  }
  return [...sets].sort((a, b) => score(b) - score(a) || a.name.localeCompare(b.name))
}

export function setsHTML(sets: CardSet[], limit = 6): string {
  if (!sets.length) {
    return `<p class="muted">A tag with 5 or more cards becomes a set. Own every card of a set in Foil, Holo or Special
      to claim its reward. <a href="#/organize">Tag cards</a> to start your first set.</p>`
  }
  const shown = sortSets(sets).slice(0, limit)
  return `<ul class="set-list">${shown.map(setHTML).join('')}</ul>
    ${sets.length > shown.length ? `<p class="muted chase-foot">${sets.length - shown.length} more sets. The ones closest to a reward are shown first.</p>` : ''}`
}

export async function renderSets(el: HTMLElement, limit = 6): Promise<void> {
  let sets: CardSet[]
  try { sets = await fetchSets() } catch { el.innerHTML = '<p class="muted">Sets could not be loaded.</p>'; return }
  el.innerHTML = setsHTML(sets, limit)
  el.onclick = async (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('[data-set]')
    if (!btn || btn.disabled) return
    btn.disabled = true
    try {
      claimed('Set reward claimed', await claimSet(Number(btn.dataset.set), btn.dataset.treatment!))
      await refreshWallet().catch(() => {})
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 409)) { btn.disabled = false; return }
    }
    await renderSets(el, limit)
  }
}
