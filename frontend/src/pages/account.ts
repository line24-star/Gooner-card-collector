/**
 * Player page: who is playing, the wallet with its history, the media folder, and the app settings
 * (sound, tours, logs). Reached from the 👤 button in the top bar and from the nav.
 */

import './account.css'
import { esc } from '../cards/card.ts'
import { fetchAccount, fetchTransactions, openLogFolder, reasonLabel, renameAccount, resetAccount, type Account, type Tx } from '../data/accountApi.ts'
import { restoreOnboarding } from '../data/onboardingApi.ts'
import { scanSummary } from '../data/scan.ts'
import { runScan } from '../ui/scanFlow.ts'
import { openMediaFolder } from '../data/settingsApi.ts'
import { completeness, pctText, reloadAll, sealedTotal, versionsText } from '../data/store.ts'
import { isMuted, setMuted } from '../fx/sound.ts'
import { openHelp } from '../help/index.ts'
import { VERSION_LABEL } from '../version.ts'
import type { Page } from '../app/router.ts'

/** "12 Nov 2026" in the local format; empty for a missing or broken date. */
export function shortDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

export function shortTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export const signed = (n: number): string => `${n > 0 ? '+' : ''}${n.toLocaleString()}`

function txRow(t: Tx): string {
  return `<tr>
    <td class="muted">${esc(shortTime(t.created_at))}</td>
    <td>${esc(reasonLabel(t.reason))}</td>
    <td class="acct-amount ${t.amount < 0 ? 'is-neg' : ''}">${signed(t.amount)}</td>
    <td class="muted acct-balance">${t.balance_after.toLocaleString()}</td>
  </tr>`
}

export const accountPage: Page = {
  id: 'account',
  title: 'Player',
  icon: '👤',
  mount(el) {
    let alive = true
    let account: Account | null = null
    let history: Tx[] = []
    let note = ''

    function render() {
      if (!account) {
        el.innerHTML = '<p class="muted">Loading your player page…</p>'
        return
      }
      const a = account
      const c = completeness()
      el.innerHTML = `
        <section class="panel acct-head">
          <div class="acct-avatar" aria-hidden="true">${esc(a.name.slice(0, 1).toUpperCase())}</div>
          <div class="acct-who">
            <form class="acct-name" data-el="name-form">
              <label class="muted" for="acct-name-input">Player name</label>
              <div class="acct-name-row">
                <input id="acct-name-input" class="input" data-el="name" value="${esc(a.name)}" maxlength="32" autocomplete="off">
                <button class="btn btn-ghost" type="submit">Save</button>
              </div>
            </form>
            <p class="muted">${a.since ? `Collecting since ${esc(shortDate(a.since))} · ` : ''}day ${a.streak_days} of your streak</p>
            <p class="acct-note" role="status" aria-live="polite">${esc(note)}</p>
          </div>
        </section>

        <section class="home-tiles">
          <div class="tile"><span class="tile-label">Coins</span><b>🪙 ${a.coins.toLocaleString()}</b><span class="muted">earned ${a.earned.toLocaleString()} · spent ${a.spent.toLocaleString()}</span></div>
          <div class="tile"><span class="tile-label">Packs</span><b>${sealedTotal().toLocaleString()} sealed</b><span class="muted">${a.packs_opened.toLocaleString()} bought so far · <a href="#/packs">your packs</a></span></div>
          <div class="tile"><span class="tile-label">Cards pulled</span><b>${a.cards_owned.toLocaleString()} / ${a.cards_total.toLocaleString()}</b><span class="muted">${(a.cards_total - a.cards_owned).toLocaleString()} still to pull</span></div>
          <div class="tile"><span class="tile-label">Collection</span><b>${pctText(c.complete)}</b><span class="muted">${versionsText(c)} held · ${c.tagged.toLocaleString()} of your ${c.owned.toLocaleString()} cards tagged</span></div>
        </section>

        <section class="panel">
          <h3>Media folder</h3>
          <code class="acct-path">${esc(a.media_folder)}</code>
          <div class="acct-actions">
            <button class="btn" type="button" data-act="scan">Scan for new media</button>
            <button class="btn btn-ghost" type="button" data-act="open-folder">Open folder</button>
            <a class="btn btn-ghost" href="#/setup">Change folder…</a>
          </div>
          <p class="muted acct-small">The app looks at this folder only when you ask: here, or when you choose a folder. New photos and videos become cards you can pull from packs.</p>
        </section>

        <section class="panel">
          <h3>Coin history</h3>
          ${a.by_reason.length ? `<ul class="acct-reasons">${a.by_reason.map((r) =>
            `<li><span>${esc(reasonLabel(r.reason))} <span class="muted">×${r.count}</span></span><b class="${r.total < 0 ? 'is-neg' : ''}">${signed(r.total)}</b></li>`).join('')}</ul>` : ''}
          ${history.length ? `<table class="acct-table">
            <thead><tr><th>When</th><th>What</th><th>Coins</th><th>Balance</th></tr></thead>
            <tbody>${history.map(txRow).join('')}</tbody>
          </table>` : '<p class="muted">Nothing yet. Tag a card or open a pack and it shows up here.</p>'}
        </section>

        <section class="panel">
          <h3>App</h3>
          <div class="acct-actions">
            <button class="btn btn-ghost" type="button" data-act="sound">${isMuted() ? 'Turn sound on' : 'Turn sound off'}</button>
            <button class="btn btn-ghost" type="button" data-act="tours">Replay a tour</button>
            <button class="btn btn-ghost" type="button" data-act="checklist">Show the getting-started list</button>
            <button class="btn btn-ghost" type="button" data-act="help">Help</button>
            <button class="btn btn-ghost" type="button" data-act="logs">Open the log folder</button>
          </div>
          <p class="muted acct-small">Logs are kept for two days in <code>${esc(a.log_folder)}</code>. Version: ${esc(VERSION_LABEL)}.
            Everything stays on this computer: one local player, no sign-in.</p>
        </section>

        <section class="panel acct-danger">
          <h3>Start over</h3>
          <p class="muted acct-small">Wipes your progress: coins, packs, pulled cards, tags, streak, the getting-started list, the tours and the market's history.
            Your photos and videos, the folder and your name stay. You start again with the welcome coins. This can't be undone.</p>
          <div class="acct-actions"><button class="btn btn-danger" type="button" data-act="reset">Reset my game…</button></div>
        </section>`
    }

    async function load() {
      try {
        const [a, h] = await Promise.all([fetchAccount(), fetchTransactions(30)])
        if (!alive) return
        account = a
        history = h
      } catch (err) {
        if (!alive) return
        el.innerHTML = `<p class="muted" role="alert">Could not load your player page: ${esc(err instanceof Error ? err.message : 'the server did not answer')}.</p>`
        return
      }
      render()
    }

    const say = (text: string) => { note = text; render() }

    el.addEventListener('submit', async (e) => {
      const form = (e.target as HTMLElement).closest('[data-el="name-form"]')
      if (!form) return
      e.preventDefault()
      const name = el.querySelector<HTMLInputElement>('[data-el="name"]')!.value
      try {
        account = await renameAccount(name)
        say('Name saved.')
      } catch (err) {
        say(err instanceof Error ? err.message : 'Could not save the name.')
      }
    })

    el.addEventListener('click', async (e) => {
      const act = (e.target as HTMLElement).closest<HTMLElement>('[data-act]')?.dataset.act
      if (!act) return
      try {
        if (act === 'scan') {
          say('Scanning your folder…')
          const st = await runScan() // asks first when the scan would add files
          if (st === 'cancelled') return say('Nothing was scanned.')
          account = await fetchAccount()
          say(scanSummary(st))
        } else if (act === 'checklist') {
          await restoreOnboarding()
          say('The getting-started list is back on Home.')
        } else if (act === 'open-folder') await openMediaFolder()
        else if (act === 'logs') await openLogFolder()
        else if (act === 'sound') {
          setMuted(!isMuted())
          window.dispatchEvent(new Event('mco:sound'))
          say(isMuted() ? 'Sound is off.' : 'Sound is on.')
        } else if (act === 'reset') {
          const typed = prompt('This wipes your coins, packs, pulled cards and tags. Your photos and videos stay.\n\nType RESET to start over.')
          if (typed === null) return
          if (typed.trim().toUpperCase() !== 'RESET') return say('Nothing was reset: you did not type RESET.')
          await resetAccount()
          await reloadAll()
          location.hash = '#/home'
        } else if (act === 'tours') void openHelp('tours')
        else if (act === 'help') void openHelp()
      } catch (err) {
        say(err instanceof Error ? err.message : 'That did not work. Try again.')
      }
    })

    // Not subscribed to the store: a re-render would wipe a name being typed. Numbers refresh on each visit.
    render()
    void load()
    return () => { alive = false }
  },
}
