/**
 * Stats: where the collection stands and what is closest. Completeness with the next milestone,
 * streak and pack counts, the treatments you own, weekly goals, sets, your latest packs and near
 * misses. It always ends in an action (open packs, tag cards).
 */

import { cardHTML, enableTilt, esc } from '../cards/card.ts'
import { visualFor } from '../cards/treatments.ts'
import { fetchHistory, type History, type RecentPack } from '../data/economyApi.ts'
import { affordable, completeness, getState, subscribe, versionsText } from '../data/store.ts'
import { openHelp } from '../help/index.ts'
import type { Card, TreatmentInfo } from '../data/types.ts'
import type { Page } from '../app/router.ts'
import { shortTime } from './account.ts'
import { renderDaily, renderGoals, renderSets } from './chase.ts'
import { milestoneText } from './home.ts'
import { openInspect } from './inspect.ts'

/** The Coins tile: the balance as text. The tile follows the store, so a claim on this page shows at once. */
export function coinsTileText(coins: number): string {
  return `🪙 ${coins.toLocaleString()}`
}

/** How far the bar under "Next milestone" is filled, 0 to 100, on the milestones' own measure. */
export function milestoneBarWidth(complete: number, next: { at: number; progress?: number } | null): number {
  if (!next || !(next.at > 0)) return 100
  return Math.max(0, Math.min(100, ((next.progress ?? complete) / next.at) * 100))
}

/** How many owned cards have each treatment as their best copy, plus the cards never pulled (of `total` cards in all). */
export function ownedBreakdown(cards: Card[], treatments: TreatmentInfo[], total = cards.length): { rows: { t: TreatmentInfo; n: number }[]; unpulled: number } {
  const owned = cards.filter((c) => c.copies > 0)
  return {
    rows: treatments.map((t) => ({ t, n: owned.filter((c) => c.treatment === t.id).length })),
    unpulled: Math.max(0, total - owned.length),
  }
}

export function packLine(p: RecentPack, packName: (id: string | null) => string, treatmentName: (id: string) => string): string {
  const parts: string[] = []
  if (p.new) parts.push(`${p.new} new`)
  if (p.better) parts.push(`${p.better} rarer`)
  if (p.version) parts.push(`${p.version} new ${p.version === 1 ? 'version' : 'versions'}`)
  if (p.upgraded) parts.push(`${p.upgraded} upgraded`)
  if (p.duplicate) parts.push(`${p.duplicate} ${p.duplicate === 1 ? 'duplicate' : 'duplicates'}`)
  const best = p.best ? ` · best pull: ${treatmentName(p.best)}` : ''
  return `${packName(p.pack)}${p.free ? ' (free)' : ''}: ${parts.join(', ') || 'no cards'}${best}`
}

export const statsPage: Page = {
  id: 'stats',
  title: 'Stats',
  icon: '◔',
  mount(el) {
    let alive = true
    const s = getState()
    const c = completeness()
    const w = s.wallet
    const { rows, unpulled } = ownedBreakdown(s.cards, s.treatments, c.total)
    const max = Math.max(1, unpulled, ...rows.map((x) => x.n))
    const ms = milestoneText(w.completeness, w.nextMilestone, w.cardsTotal)
    const msWidth = milestoneBarWidth(w.completeness, w.nextMilestone)
    const std = s.packTypes[0]
    const n = std ? affordable(std) : 0
    const packName = (id: string | null) => s.packTypes.find((p) => p.id === id)?.name ?? 'Pack'
    const treatmentName = (id: string) => s.treatments.find((t) => t.id === id)?.name ?? id
    enableTilt(el)

    el.innerHTML = `
      <section class="stats-hero">
        <div class="stats-big">
          <span class="stats-pct">${(c.complete * 100).toFixed(1)}%</span>
          <span class="muted">of all versions held · ${versionsText(c)} · ${c.owned.toLocaleString()} of ${c.total.toLocaleString()} cards pulled · ${c.tagged.toLocaleString()} of them tagged</span>
          <div class="progress progress-lg"><div class="progress-fill" style="width:${c.complete * 100}%"></div></div>
          ${ms ? `<div class="stats-next">
            <b>Next milestone:</b> ${esc(ms)}
            <div class="progress"><div class="progress-fill" style="width:${msWidth}%"></div></div>
          </div>` : '<p class="stats-next"><b>Every milestone is paid out.</b> The chase now is the versions still missing.</p>'}
          <p class="muted stats-note">Completeness counts versions. Every card in your folder has three: Foil, Holo and Special. A version counts while you hold a copy of it.
          Normal copies are the base for merging and don't count, and tags don't either. Selling or merging your last copy of a version takes it off again, and every new file adds three open versions.</p>
          <div class="home-actions">
            <a class="btn" href="#/packs">${n ? `Open a pack · you can afford ×${n}` : 'Open packs'}</a>
            <a class="btn btn-ghost" href="#/organize">Tag cards to earn coins</a>
            <button class="btn btn-ghost" type="button" data-act="earn-help">How to earn coins</button>
          </div>
        </div>
      </section>

      <section class="home-tiles">
        <div class="tile"><span class="tile-label">Coins</span><b data-el="coins">${coinsTileText(w.coins)}</b><a href="#/account">Coin history</a></div>
        <div class="tile"><span class="tile-label">Streak</span><b>Day ${w.streakDays}</b>
          <span class="muted">${w.canCheckIn ? 'Check in on Home to unlock your free pack' : 'Tomorrow: the next free pack'}</span></div>
        <div class="tile"><span class="tile-label">Packs today</span><b data-el="packs-today">…</b>
          <span class="muted" data-el="packs-line">${w.packsOpened.toLocaleString()} bought in total</span></div>
      </section>

      <div data-el="daily"></div>

      <section class="panel">
        <h3>Weekly goals</h3>
        <div data-el="goals"><p class="muted">Loading…</p></div>
      </section>

      <section class="panel">
        <h3>Sets</h3>
        <div data-el="sets"><p class="muted">Loading…</p></div>
      </section>

      <section class="panel">
        <h3>Your binder by treatment</h3>
        <ul class="rarity-list">
          ${rows.map(({ t, n: count }) => `
            <li style="--rarity:${visualFor(t.id).color}">
              <a class="rarity-name" href="#/collection?filter=${esc(t.id)}">${esc(t.name)}</a>
              <span class="rarity-bar"><span style="width:${(count / max) * 100}%"></span></span>
              <span class="rarity-n">${count.toLocaleString()}</span>
            </li>`).join('')}
          <li style="--rarity:var(--muted)">
            <span class="rarity-name">Not pulled</span>
            <span class="rarity-bar"><span style="width:${(unpulled / max) * 100}%"></span></span>
            <span class="rarity-n">${unpulled.toLocaleString()}</span>
          </li>
        </ul>
      </section>

      <section class="panel">
        <h3>Latest packs</h3>
        <div data-el="packs"><p class="muted">Loading…</p></div>
      </section>

      <section class="panel">
        <h3>Near misses</h3>
        <div data-el="near"><p class="muted">Loading…</p></div>
      </section>`

    void renderDaily(el.querySelector<HTMLElement>('[data-el="daily"]')!)
    void renderGoals(el.querySelector<HTMLElement>('[data-el="goals"]')!)
    void renderSets(el.querySelector<HTMLElement>('[data-el="sets"]')!)

    const fill = (h: History) => {
      const line = el.querySelector('[data-el="packs-line"]')
      if (line) line.textContent = `${h.packs_this_week} this week · ${w.packsOpened.toLocaleString()} bought in total`
      const today = el.querySelector('[data-el="packs-today"]')
      if (today) today.textContent = String(h.packs_today)
      const packs = el.querySelector<HTMLElement>('[data-el="packs"]')!
      packs.innerHTML = h.recent_packs.length
        ? `<ul class="stats-packs">${h.recent_packs.map((p) => `
            <li style="--rarity:${p.best ? visualFor(p.best).color : 'var(--muted)'}">
              <span class="muted">${esc(shortTime(p.at))}</span>
              <span>${esc(packLine(p, packName, treatmentName))}${p.near_misses ? ` · <b class="stats-near">${p.near_misses} so close</b>` : ''}</span>
            </li>`).join('')}</ul>`
        : '<p class="muted">No packs opened yet. <a href="#/packs">Open your first pack.</a></p>'
      const near = el.querySelector<HTMLElement>('[data-el="near"]')!
      near.innerHTML = h.near_misses.length
        ? `<p class="muted">These pulls landed just short of the next treatment. ${h.near_misses_this_week} this week.</p>
           <div class="home-pull-row">${h.near_misses.map((m) => cardHTML(m.card, { size: 'sm' })).join('')}</div>`
        : '<p class="muted">No near misses yet. When a pull lands just short of the next treatment, it shows up here.</p>'
    }
    fetchHistory().then((h) => { if (alive) fill(h) }).catch(() => {
      if (!alive) return
      for (const name of ['packs', 'near']) el.querySelector<HTMLElement>(`[data-el="${name}"]`)!.innerHTML = '<p class="muted">Could not load this right now.</p>'
    })

    el.addEventListener('click', (e) => {
      if ((e.target as HTMLElement).closest('[data-act="earn-help"]')) return void openHelp('earning-coins')
      const card = (e.target as HTMLElement).closest<HTMLElement>('.home-pull-row .card')
      if (card) openInspect(Number(card.dataset.cardId))
    })
    // A claim on this page (a goal, a set, a collector level) changes the balance: the tile follows it.
    const off = subscribe((now) => {
      const tile = el.querySelector<HTMLElement>('[data-el="coins"]')
      const text = coinsTileText(now.wallet.coins)
      if (tile && tile.textContent !== text) tile.textContent = text
    })
    return () => { alive = false; off() }
  },
}
