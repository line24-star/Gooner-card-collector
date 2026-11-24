import './styles/tokens.css'
import './style.css'
import './cards/cards.css'
import { remountOn, startRouter, type Page } from './app/router.ts'
import { renderShell, setActiveNav } from './app/shell.ts'
import { installErrorReporting } from './data/clientLog.ts'
import { getState, initData, loadConfig, onLibraryChange, subscribe } from './data/store.ts'
import { accountPage } from './pages/account.ts'
import { SETUP_ID, setupPage, shouldShowSetup } from './pages/setup.ts'
import { initHelp, openHelp } from './help/index.ts'
import { onServerState, startPresence, stopServer, type ServerState } from './lifecycle.ts'
import { confirmDialog } from './ui/confirm.ts'
import { collectionPage } from './pages/collection.ts'
import { homePage } from './pages/home.ts'
import { marketPage } from './pages/market.ts'
import { organizePage } from './pages/organize.ts'
import { packsPage } from './pages/packs.ts'
import { statsPage } from './pages/stats.ts'

installErrorReporting()

// Collection and Organize follow library changes (e.g. new files dropped into the folder).
const followLibrary = (p: Page): Page => ({ ...p, mount: remountOn(onLibraryChange, p.mount) })
const PAGES = [homePage, followLibrary(collectionPage), followLibrary(organizePage), packsPage, marketPage, statsPage]

const outlet = renderShell(document.querySelector<HTMLDivElement>('#app')!, [...PAGES, accountPage])

const statusEl = document.querySelector<HTMLSpanElement>('#status')!
const stopBtn = document.querySelector<HTMLButtonElement>('#stop')!
const stoppedEl = document.querySelector<HTMLDivElement>('#stopped')!

const LABELS: Record<ServerState, string> = {
  connecting: 'Connecting…',
  online: 'Online',
  offline: 'Reconnecting…',
  stopped: 'Stopped',
}

onServerState((s) => {
  statusEl.textContent = LABELS[s]
  statusEl.dataset.state = s
  stopBtn.disabled = s === 'stopped'
  stoppedEl.hidden = s !== 'stopped'
})

stopBtn.addEventListener('click', async () => {
  if (!(await confirmDialog({ title: 'Stop the server?', text: 'The app closes. Your collection is saved. Run start.bat to start again.', confirm: 'Stop the server', cancel: 'Keep playing' }))) return
  await stopServer()
})
document.querySelector<HTMLButtonElement>('#help')!.addEventListener('click', () => void openHelp())

startPresence()

// Without cards there is nothing to play with: every route leads to the setup screen until the
// media folder holds at least one photo or video.
const guard = (id: string): string | null => (shouldShowSetup(getState().mode) && id !== SETUP_ID ? SETUP_ID : null)

// Pages read the store when they mount, so load config and cards first.
void Promise.all([loadConfig(), initData()])
  .then(() => {
    startRouter(outlet, [...PAGES, accountPage, setupPage], (p) => setActiveNav(p.id), guard)
    // The library can become empty while playing (the folder was switched or emptied).
    subscribe((s) => { if (shouldShowSetup(s.mode) && !location.hash.startsWith(`#/${SETUP_ID}`)) location.hash = `#/${SETUP_ID}` })
    void initHelp() // after the route is set, so it can see #/setup and leave the welcome tour to that page
  })
