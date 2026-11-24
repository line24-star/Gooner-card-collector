/**
 * Help system entry point: onboarding + Help Center + tours.
 *
 *   initHelp()      – wire up the "?" shortcut and start onboarding on first run
 *   openHelp(x)     – open the Help Center (optionally on a tab or article id)
 *   startTour(id)   – run a tour and record the result
 */

import './help.css'
import { initHelpCenter, openHelp } from './helpCenter.ts'
import { getState, refreshWallet } from '../data/store.ts'
import { rewardText } from '../data/economyApi.ts'
import { play } from '../fx/sound.ts'
import { shouldRunFirstVisit } from './firstVisit.ts'
import { overlayOpen } from '../ui/overlay.ts'
import { toast } from '../ui/toast.ts'
import { fetchProgress, markTutorial } from './progress.ts'
import { isTourActive, runTour } from './tour.ts'
import { ONBOARDING_TOUR_ID, findTour } from './tours.ts'

export { openHelp, shouldRunFirstVisit }

export async function startTour(id: string): Promise<void> {
  const tour = findTour(id)
  if (!tour || isTourActive()) return
  if (tour.page && !location.hash.startsWith(`#/${tour.page}`)) {
    location.hash = `#/${tour.page}`
    await new Promise((r) => setTimeout(r, 200)) // let the router mount the page
  }
  const result = await runTour(tour)
  try {
    const saved = await markTutorial(id, result)
    const packs = rewardText(saved.packs)
    if (result === 'completed' && packs) {
      play('coin')
      toast(`Every tour taken: +${packs}`, { kind: 'good', action: { label: 'Open a pack', href: '#/packs' } })
      await refreshWallet()
    }
  } catch (err) {
    console.warn('Could not save tutorial progress', err)
  }
}

/** Runs tour `id` once, the first time its page/panel is seen (never stacks on other tours or setup). */
export async function firstVisitTour(id: string): Promise<void> {
  try {
    if (isTourActive() || getState().mode !== 'live' || location.hash.startsWith('#/setup') || overlayOpen()) return
    const progress = await fetchProgress()
    const ok = shouldRunFirstVisit({ mode: getState().mode, progress, tourActive: isTourActive(), id, hash: location.hash })
    if (!ok) return
    const panel = id === 'card-panel' ? document.querySelector('dialog.inspect[open]') : null
    await new Promise((r) => setTimeout(r, 400)) // let the page settle before dimming it
    // Re-check: something may have started (or the player moved on) while we waited.
    if (isTourActive() || location.hash.startsWith('#/setup') || overlayOpen()) return // never behind a pack being opened
    const page = findTour(id)?.page
    if (page && !location.hash.startsWith(`#/${page}`)) return // the player already moved on
    if (id === 'card-panel' && !(panel instanceof HTMLDialogElement && panel.open)) return // panel already closed
    await startTour(id)
  } catch {
    // Backend unreachable: skip.
  }
}

/** Leaving #/setup by nav/Back (not its own buttons) must not wedge the welcome tour. */
async function welcomeAfterSetup(): Promise<void> {
  await new Promise((r) => setTimeout(r, 600)) // setup's own buttons start the tour first
  if (isTourActive() || location.hash.startsWith('#/setup')) return
  try {
    const progress = await fetchProgress()
    if (!progress.has(ONBOARDING_TOUR_ID) && !isTourActive() && !location.hash.startsWith('#/setup')) {
      await startTour(ONBOARDING_TOUR_ID)
    }
  } catch { /* backend unreachable: skip */ }
}

export async function initHelp(): Promise<void> {
  initHelpCenter({ startTour: (id) => void startTour(id) })
  window.addEventListener('hashchange', (e) => {
    if (new URL(e.oldURL).hash.startsWith('#/setup') && !new URL(e.newURL).hash.startsWith('#/setup')) void welcomeAfterSetup()
  })
  try {
    // The first-run setup screen starts the welcome tour itself when the player leaves it.
    if (location.hash.startsWith('#/setup')) return
    const progress = await fetchProgress()
    if (location.hash.startsWith('#/setup')) return
    if (!progress.has(ONBOARDING_TOUR_ID)) await startTour(ONBOARDING_TOUR_ID)
  } catch {
    // Backend unreachable: skip onboarding rather than nag on every load.
  }
}
