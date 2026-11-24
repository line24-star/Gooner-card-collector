/**
 * Setup: welcome, the media folder (open it, or choose another with the system folder dialog) and a
 * live count of imported cards. While the collection has no cards this is the only screen (main.ts
 * guards every route), and "Start playing" stays disabled until at least one card is imported.
 * Players with cards reach it from the Player page to change their folder.
 */

import './setup.css'
import { navigate, type Page } from '../app/router.ts'
import { esc } from '../cards/card.ts'
import { mediaTotal, scanStatus, shouldReload, type ScanStatus } from '../data/api.ts'
import { watchScan } from '../data/scan.ts'
import { runScan } from '../ui/scanFlow.ts'
import {
  NeedsConfirmError, getMediaFolder, openMediaFolder, pickMediaFolder, resetMediaFolder, setMediaFolder, type MediaFolderInfo,
} from '../data/settingsApi.ts'
import { getState, initData } from '../data/store.ts'
import { startTour } from '../help/index.ts'
import { fetchProgress, markTutorial, type TutorialStatus } from '../help/progress.ts'
import { ONBOARDING_TOUR_ID } from '../help/tours.ts'

export const SETUP_ID = 'setup'

/** The setup screen is forced on the player exactly while the collection has no cards. */
export function shouldShowSetup(mode: 'empty' | 'live'): boolean {
  return mode === 'empty'
}

/** The welcome-coins line on the setup screen; empty when the amount isn't known. */
export function welcomeCoinsText(coins: number | null): string {
  if (coins === null || coins <= 0) return ''
  return `You start with ${coins.toLocaleString()} welcome coins, enough for your first packs. ` +
    `Importing your library doesn't earn coins; photos and videos you add later do.`
}

const POLL_MS = 2000

/**
 * Repeats `task` every `delayMs`, never running two at once. `trigger()` runs it now: if a run is
 * in flight it is queued to run once more right after (any number of triggers collapse into one).
 * A task error is swallowed (the next tick retries). `stop()` ends the loop.
 */
export function createPoller(task: () => Promise<void>, delayMs: number): { trigger: () => Promise<void>; stop: () => void } {
  let timer: ReturnType<typeof setTimeout> | undefined
  let running = false
  let again = false
  let stopped = false
  const run = async (): Promise<void> => {
    if (stopped) return
    if (running) { again = true; return }
    running = true
    clearTimeout(timer)
    try {
      do {
        again = false
        try { await task() } catch { /* server briefly unreachable */ }
      } while (again && !stopped)
    } finally {
      running = false
    }
    if (!stopped) timer = setTimeout(() => void run(), delayMs)
  }
  return { trigger: run, stop: () => { stopped = true; clearTimeout(timer) } }
}

export const setupPage: Page = {
  id: SETUP_ID,
  title: 'Set up',
  icon: '⚙',
  mount(el) {
    let alive = true
    let count = 0
    let scan: ScanStatus | null = null
    let folder: MediaFolderInfo | null = null
    let pendingPath = '' // a 409 is waiting for "Switch anyway"

    el.innerHTML = `
      <section class="setup" aria-labelledby="setup-title">
        <div class="setup-step">
          <span class="setup-num" aria-hidden="true">1</span>
          <div>
            <h1 id="setup-title" class="setup-title">Welcome</h1>
            <p class="setup-lead">Turn your photos and videos into a card collection: open packs, tag your cards and fill your album.</p>
            <p class="setup-welcome" data-el="welcome" hidden></p>
          </div>
        </div>

        <div class="setup-step">
          <span class="setup-num" aria-hidden="true">2</span>
          <div class="setup-body">
            <h2 class="setup-h">Your media folder</h2>
            <div class="setup-warn" data-el="unavailable" role="alert" hidden></div>
            <code class="setup-path" data-el="path">…</code>
            <div class="setup-actions">
              <button type="button" class="btn" data-el="pick">Choose folder…</button>
              <button type="button" class="btn btn-ghost" data-el="open">Open this folder</button>
              <button type="button" class="btn btn-ghost" data-el="reset" hidden>Use the default folder</button>
              <button type="button" class="setup-link" data-el="change">Type a path instead</button>
            </div>
            <form class="setup-change" data-el="form" hidden>
              <label class="setup-label" for="setup-path-input">Full path to your folder</label>
              <div class="setup-row">
                <input id="setup-path-input" class="setup-input" data-el="input" autocomplete="off" spellcheck="false" placeholder="C:\\Users\\you\\Pictures">
                <button type="submit" class="btn" data-el="save">Save</button>
              </div>
            </form>
            <p class="setup-msg" data-el="msg" role="alert" hidden></p>
            <div class="setup-confirm" data-el="confirm" hidden>
              <span data-el="confirm-text"></span>
              <button type="button" class="btn" data-el="confirm-go">Switch anyway</button>
            </div>
            <p class="setup-hint"><b>Choose folder…</b> opens the normal folder window so you can pick the folder that already holds your photos and videos; it is scanned right away. If you copy files into the folder shown above instead, press <b>Scan folder</b> afterwards: the app never scans by itself. Subfolders are fine. Your files are never changed.</p>
          </div>
        </div>

        <div class="setup-step">
          <span class="setup-num" aria-hidden="true">3</span>
          <div class="setup-body">
            <h2 class="setup-h">Import</h2>
            <div class="setup-count" role="status" aria-live="polite">
              <span class="setup-spinner" data-el="spinner" aria-hidden="true"></span>
              <span class="setup-count-num" data-el="count">0</span>
              <span class="setup-count-label" data-el="count-label">Waiting for files…</span>
              <button type="button" class="btn btn-ghost" data-el="scan">Scan folder</button>
            </div>
          </div>
        </div>

        <div class="setup-foot">
          <button type="button" class="btn setup-go" data-el="start" disabled>Start playing</button>
          <span class="setup-need muted" data-el="need">Add at least one photo or video to start.</span>
        </div>
      </section>`

    const q = <T extends HTMLElement>(name: string) => el.querySelector<T>(`[data-el="${name}"]`)!
    const pathEl = q('path')
    const warnEl = q('unavailable')
    const resetBtn = q<HTMLButtonElement>('reset')
    const form = q<HTMLFormElement>('form')
    const input = q<HTMLInputElement>('input')
    const msgEl = q('msg')
    const confirmEl = q('confirm')
    const confirmText = q('confirm-text')
    const startBtn = q<HTMLButtonElement>('start')
    const welcomeEl = q('welcome')
    welcomeEl.textContent = welcomeCoinsText(getState().startingCoins)
    welcomeEl.hidden = !welcomeEl.textContent

    const showMsg = (text: string) => { msgEl.textContent = text; msgEl.hidden = !text }
    const clearNotices = () => { showMsg(''); confirmEl.hidden = true; pendingPath = '' }

    const renderFolder = () => {
      if (!folder) return
      pathEl.innerHTML = esc(folder.path)
      resetBtn.hidden = !folder.overridden
      const bad = folder.saved_unavailable
      warnEl.hidden = !bad
      // Built with esc: the path is untrusted text.
      warnEl.innerHTML = bad
        ? `Your saved folder <code>${esc(bad)}</code> isn't available right now; using the default.`
        : ''
    }

    const renderCount = () => {
      q('count').textContent = count.toLocaleString()
      const importing = scan?.state === 'scanning'
      q('spinner').hidden = count > 0 && !importing
      q('count-label').textContent = importing
        ? `Importing… ${scan!.done.toLocaleString()} / ${scan!.total.toLocaleString()}`
        : count === 0 ? 'No cards yet. Choose a folder, or press Scan folder.' : count === 1 ? 'card found' : 'cards found'
      q<HTMLButtonElement>('scan').disabled = importing
      startBtn.disabled = count < 1
      q('need').hidden = count > 0
    }

    const refreshFolder = async () => {
      try { folder = await getMediaFolder(); renderFolder() } catch { /* keep the last known folder */ }
    }

    // One poll in flight and one timer at a time; a trigger during a poll re-polls once afterwards.
    const poller = createPoller(async () => {
      const next = await scanStatus()
      const prev = scan
      scan = next
      if (shouldReload(prev, next)) await initData()
      count = await mediaTotal()
      if (alive) renderCount()
    }, POLL_MS)

    let applying = false
    const setApplying = (on: boolean) => {
      applying = on
      for (const name of ['save', 'confirm-go', 'reset', 'pick']) q<HTMLButtonElement>(name).disabled = on
    }

    const applyChange = async (fn: () => Promise<MediaFolderInfo>, path: string) => {
      if (applying) return
      setApplying(true)
      clearNotices()
      try {
        await fn()
        // The response's media_count lags the rescan; read the folder again and keep polling.
        await refreshFolder()
        clearNotices()
        form.hidden = true
        input.value = ''
        void watchScan(true) // choosing a folder scans it: show the progress bar too
        void poller.trigger()
      } catch (err) {
        if (err instanceof NeedsConfirmError) {
          pendingPath = path
          confirmText.textContent = `Your collection already has ${err.mediaCount.toLocaleString()} cards. Switching hides them until you switch back.`
          confirmEl.hidden = false
        } else {
          showMsg(err instanceof Error ? err.message : 'Could not change the folder.')
        }
      } finally {
        setApplying(false)
      }
    }

    q<HTMLButtonElement>('open').addEventListener('click', async () => {
      clearNotices()
      try { await openMediaFolder() } catch (err) { showMsg(err instanceof Error ? err.message : 'Could not open the folder.') }
    })
    // The system "Select folder" dialog runs on the server (it is this computer); the page only
    // receives the chosen path and then switches to it like a typed one.
    const pickBtn = q<HTMLButtonElement>('pick')
    pickBtn.addEventListener('click', async () => {
      if (applying || pickBtn.disabled) return
      clearNotices()
      pickBtn.disabled = true
      const label = pickBtn.textContent
      pickBtn.textContent = 'Waiting for the folder window…'
      let path: string | null = null
      try {
        path = await pickMediaFolder()
      } catch (err) {
        showMsg(err instanceof Error ? err.message : 'Could not open the folder window. Type the path instead.')
        form.hidden = false
      } finally {
        pickBtn.disabled = false
        pickBtn.textContent = label
      }
      if (path) void applyChange(() => setMediaFolder(path!), path)
    })
    q<HTMLButtonElement>('scan').addEventListener('click', async () => {
      clearNotices()
      void poller.trigger()
      // With cards already in the library the scan asks first (new files add version slots); the first import does not.
      try { await runScan() } catch (err) { showMsg(err instanceof Error ? err.message : 'Could not scan the folder.') }
      void poller.trigger()
    })
    q<HTMLButtonElement>('change').addEventListener('click', () => {
      form.hidden = !form.hidden
      if (!form.hidden) input.focus()
    })
    form.addEventListener('submit', (e) => {
      e.preventDefault()
      const path = input.value.trim()
      if (!path) { showMsg('Enter the full path to a folder.'); return }
      void applyChange(() => setMediaFolder(path), path)
    })
    q<HTMLButtonElement>('confirm-go').addEventListener('click', () => {
      const path = pendingPath
      void applyChange(path ? () => setMediaFolder(path, true) : () => resetMediaFolder(true), path)
    })
    resetBtn.addEventListener('click', () => { pendingPath = ''; void applyChange(() => resetMediaFolder(), '') })

    let leaving = false
    const leave = async (status: TutorialStatus) => {
      if (leaving) return
      leaving = true
      startBtn.disabled = true
      try { await markTutorial(SETUP_ID, status) } catch (err) { console.warn('Could not save setup progress', err) }
      await initData()
      navigate('home')
      try {
        const progress = await fetchProgress()
        if (!progress.has(ONBOARDING_TOUR_ID)) void startTour(ONBOARDING_TOUR_ID)
      } catch { /* backend unreachable: skip the tour */ }
    }
    startBtn.addEventListener('click', () => void leave('completed'))

    renderCount()
    void refreshFolder()
    void poller.trigger()

    return () => {
      alive = false
      poller.stop()
    }
  },
}
