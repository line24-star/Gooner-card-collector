/**
 * "Getting started" checklist in Home's side bar: steps, progress, Claim buttons, a Next highlight,
 * and the tours' own reward (v0.9.0: the tours pay their packs once, when all of them are completed).
 */

import './checklist.css'
import { esc } from '../cards/card.ts'
import { ApiError } from '../data/api.ts'
import { claimOnboarding, dismissOnboarding, fetchOnboarding, toursRewardText, type Checklist, type ChecklistStep } from '../data/onboardingApi.ts'
import { getState, refreshWallet } from '../data/store.ts'
import { rewardText } from '../data/economyApi.ts'
import { floatText } from '../fx/effects.ts'
import { play } from '../fx/sound.ts'
import { openHelp } from '../help/index.ts'

export type { ChecklistStep }

const GO: Record<string, string> = {
  'add-media': '#/setup',
  'tag-5': '#/organize',
  'first-pack': '#/packs',
  'tag-pulled': '#/collection?filter=owned',
  upgrade: '#/collection?filter=owned',
}

export const goLink = (id: string): string => GO[id] ?? '#/home'

/** First step the player still has to finish (a done-but-unclaimed step counts: claim it). */
export function nextStep(steps: ChecklistStep[]): ChecklistStep | null {
  return steps.find((s) => !(s.done && s.claimed)) ?? null
}

/** `onChange`: a reward was claimed (the sealed packs changed). */
export async function renderChecklist(el: HTMLElement, onChange?: () => void): Promise<void> {
  if (getState().mode !== 'live') {
    el.innerHTML = `
      <section class="checklist" data-tour="checklist">
        <div class="checklist-head"><h2>Getting started</h2></div>
        <ul class="checklist-list"><li class="checklist-row is-next">
          <span class="checklist-title">Add your photos and videos</span>
          <span></span><span></span>
          <a class="btn checklist-action" href="#/setup">Go</a>
        </li></ul>
      </section>`
    return
  }
  let data: Checklist
  try { data = await fetchOnboarding() } catch { el.innerHTML = ''; return }
  const tours = toursRewardText(data.tours)
  const toursOpen = Boolean(tours) && !data.tours?.claimed
  // Hidden by the player, or everything done and paid: nothing is shown (the tours' line stays while their reward is open).
  if (data.dismissed || (data.complete && data.steps.every((s) => s.claimed) && !toursOpen)) { el.innerHTML = ''; return }

  const next = nextStep(data.steps)
  el.innerHTML = `
    <section class="checklist" data-tour="checklist">
      <div class="checklist-head">
        <h2>Getting started</h2>
        <button class="checklist-hide" type="button" data-hide>Hide</button>
      </div>
      <ul class="checklist-list">
        ${data.steps.map((s) => rowHTML(s, s.id === next?.id)).join('')}
      </ul>
      ${tours ? `<p class="checklist-tours" data-el="tours-reward"><span aria-hidden="true">🎓</span> ${esc(tours)} <button class="checklist-hide" type="button" data-tours>See the tours</button></p>` : ''}
    </section>`

  el.querySelector('[data-hide]')?.addEventListener('click', async () => {
    try { await dismissOnboarding() } catch { return }
    el.innerHTML = ''
  })
  el.querySelector('[data-tours]')?.addEventListener('click', () => void openHelp('tours'))
  el.querySelectorAll<HTMLButtonElement>('[data-claim]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (btn.disabled) return
      btn.disabled = true
      try {
        const r = await claimOnboarding(btn.dataset.claim!)
        const packs = rewardText(r.packs)
        if (packs) floatText(`+${packs}`, at(btn))
        play('coin')
        await refreshWallet().catch(() => {})
      } catch (e) {
        if (!(e instanceof ApiError && e.status === 409)) { btn.disabled = false; return }
      }
      await renderChecklist(el, onChange)
      onChange?.()
    })
  })
}

const at = (el: Element) => { const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top } }

function rowHTML(s: ChecklistStep, isNext: boolean): string {
  // v0.9.0: only some steps pay (a pack). A step without a reward is done, with nothing to claim.
  const reward = rewardText(s.reward)
  const pct = s.target ? Math.min(100, Math.round((s.progress / s.target) * 100)) : 0
  const action = s.claimed
    ? `<span class="muted checklist-action">${reward ? 'Claimed' : 'Done'}</span>`
    : s.done && reward
      ? `<button class="btn checklist-action" type="button" data-claim="${esc(s.id)}">Claim ${esc(reward)}</button>`
      : `<a class="btn btn-ghost checklist-action" href="${goLink(s.id)}">Go</a>`
  return `
    <li class="checklist-row${isNext ? ' is-next' : ''}${s.claimed ? ' is-claimed' : ''}">
      <span class="checklist-title">${s.claimed ? '✓ ' : ''}${esc(s.title)}${isNext ? ' <span class="checklist-next">Next</span>' : ''}</span>
      <span class="checklist-bar"><span class="progress"><span class="progress-fill" style="width:${pct}%"></span></span>${s.progress}/${s.target}</span>
      <span class="checklist-reward">${esc(reward)}</span>
      ${action}
    </li>`
}
