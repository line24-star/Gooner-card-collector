/** App chrome: top bar (brand, coins, the version pill, value, status, sound, help, stop) + nav + outlet. */

import { health } from '../data/api.ts'
import { visualFor } from '../cards/treatments.ts'
import { onScanStatus, watchScan } from '../data/scan.ts'
import { affordable, completeness, getState, onSlotsChange, packCost, pctText, sealedTotal, subscribe, versionDeltaText } from '../data/store.ts'
import type { SlotsChange } from '../data/types.ts'
import { countTo } from '../fx/effects.ts'
import { isMuted, setMuted } from '../fx/sound.ts'
import { initInbox } from '../ui/inbox.ts'
import { runScan } from '../ui/scanFlow.ts'
import { toast } from '../ui/toast.ts'
import { VERSION_LABEL } from '../version.ts'
import { setTitlePrefix, type Page } from './router.ts'

/** "▲ 2.4%", "▼ 0.3%" or "– 0%": how the collection moved at the last price update. One decimal under 10%. */
export function valueChangeText(change: number): string {
  const pct = Math.abs(change) * 100
  if (pct < 0.05) return '– 0%'
  const shown = pct < 10 ? pct.toFixed(1).replace(/\.0$/, '') : String(Math.round(pct))
  return `${change > 0 ? '▲' : '▼'} ${shown}%`
}

/** The mark on the Packs button: how many sealed packs wait, or "free" when only today's free pack does. '' = nothing. */
export function packsBadge(sealed: number, freeReady: boolean): string {
  return sealed > 0 ? (sealed > 99 ? '99+' : String(sealed)) : freeReady ? 'free' : ''
}
export function packsTitle(sealed: number, canBuy: number, freeReady: boolean): string {
  const parts = [sealed > 0 ? `${sealed} sealed ${sealed === 1 ? 'pack' : 'packs'} to open` : 'No sealed packs']
  if (freeReady) parts.push('your free pack is ready')
  parts.push(canBuy > 0 ? `you can afford ×${canBuy}` : 'not enough coins for a pack')
  return parts.join(' · ')
}

/** How long the delta stays next to the version pill's numbers. */
export const PILL_DELTA_MS = 2000

/**
 * What the pill appends for 2 seconds after a change: "+2" or "−1" for versions filled or emptied,
 * "+36 slots" when a scan added files (the total grew). '' when nothing is worth showing.
 */
export function pillDeltaText(c: Pick<SlotsChange, 'delta' | 'total_delta'>): string {
  const versions = versionDeltaText(c.delta)
  const total = c.total_delta ? `${versionDeltaText(c.total_delta)} ${Math.abs(c.total_delta) === 1 ? 'slot' : 'slots'}` : ''
  return [versions, total].filter(Boolean).join(' · ')
}

/** The pill's tooltip: the percentage and the pulled count live here, not in the pill. */
export function pillTitle(c: { owned: number; total: number; complete: number }): string {
  return `You hold ${pctText(c.complete)} of all versions. Every card has a Foil, a Holo and a Special; Normal copies don't count. ` +
    `${c.owned.toLocaleString()} of the ${c.total.toLocaleString()} cards in your folder pulled. Click for the cards with missing versions.`
}

/** Badge text for a non-main profile, or null for the player's own (main). */
export function profileBadge(profile: string | undefined): string | null {
  return profile && profile !== 'main' ? `TEST · ${profile}` : null
}

export function renderShell(root: HTMLElement, pages: Page[]): HTMLElement {
  root.innerHTML = `
  <header class="topbar">
    <a class="brand" href="#/home"><span class="brand-mark">✦</span><span class="brand-name">Media Collection</span></a>
    <div class="topbar-stats">
      <span class="pill pill-coins" data-tour="coins" title="Coins"><span aria-hidden="true">🪙</span> <b id="coin-count">0</b></span>
      <a class="pill pill-versions" href="#/collection?filter=missing&from=pill" data-tour="versions-pill"><b id="ver-filled">0</b><span class="muted">/</span><span id="ver-total">0</span><span class="muted">versions</span><b id="ver-delta" class="pill-delta" role="status" hidden></b></a>
      <a class="pill pill-value" href="#/market" data-tour="value-pill" title="What all your cards are worth on the market today. Click for the market."><span aria-hidden="true">📈</span> <b id="value-count">0</b><span class="muted"> value</span> <b id="value-change" class="pill-change"></b></a>
    </div>
    <div class="topbar-right">
      <button id="scan" type="button" class="scan-btn" data-tour="scan" title="Look in your media folder for new photos and videos">⟳ Scan</button>
      <span id="status" class="status" data-tour="status"></span>
      <button id="inbox" type="button" class="icon-btn inbox-btn" data-tour="inbox" aria-expanded="false" title="Messages" aria-label="Messages"><span class="inbox-icon-env" aria-hidden="true">✉</span><span class="inbox-flag" hidden></span><span class="inbox-badge" hidden></span></button>
      <a id="account-link" class="icon-btn" href="#/account" data-tour="account" title="Your player page" aria-label="Your player page">👤</a>
      <button id="mute" type="button" class="icon-btn" aria-label="Toggle sound"></button>
      <button id="help" type="button" class="icon-btn" data-tour="help" title="Help (?)" aria-label="Help">?</button>
      <button id="stop" type="button" class="stop" data-tour="stop">Stop</button>
    </div>
  </header>
  <div class="import-bar" id="import-bar" role="status" hidden><span id="import-text"></span><progress id="import-progress" max="1" value="0"></progress></div>
  <main id="content" class="content" data-tour="content" tabindex="-1"></main>
  <nav class="nav" data-tour="nav" aria-label="Main">
    ${pages.map((p) => `<a href="#/${p.id}" data-nav="${p.id}"><span class="nav-icon" aria-hidden="true">${p.icon}</span><span>${p.title}</span>${p.id === 'packs' ? '<span class="nav-badge" id="packs-badge" hidden></span>' : ''}</a>`).join('')}
  </nav>
  <div class="version-tag" title="Version"><span id="profile-badge" class="profile-badge" hidden></span>${VERSION_LABEL}</div>
  <div id="stopped" class="stopped" hidden>
    <div>
      <h2>Server stopped</h2>
      <p>You can close this tab. Run <code>start.bat</code> to start again.</p>
    </div>
  </div>`

  const coinEl = root.querySelector<HTMLElement>('#coin-count')!
  const filledEl = root.querySelector<HTMLElement>('#ver-filled')!
  const totalEl = root.querySelector<HTMLElement>('#ver-total')!
  const deltaEl = root.querySelector<HTMLElement>('#ver-delta')!
  const versionPill = root.querySelector<HTMLElement>('.pill-versions')!
  const muteBtn = root.querySelector<HTMLButtonElement>('#mute')!
  const valueEl = root.querySelector<HTMLElement>('#value-count')!
  const changeEl = root.querySelector<HTMLElement>('#value-change')!
  const packsBadgeEl = root.querySelector<HTMLElement>('#packs-badge')!
  const packsLink = root.querySelector<HTMLElement>('[data-nav="packs"]')!
  const valuePill = root.querySelector<HTMLElement>('.pill-value')!
  initInbox(root.querySelector<HTMLButtonElement>('#inbox')!)
  const scanBtn = root.querySelector<HTMLButtonElement>('#scan')!
  scanBtn.addEventListener('click', async () => {
    if (scanBtn.disabled) return
    scanBtn.disabled = true
    scanBtn.textContent = '⟳ Scanning…'
    try {
      await runScan() // asks first when it would add files, and says what it found
    } catch (err) {
      toast(err instanceof Error ? err.message : 'The scan did not start. Try again.', { kind: 'warn' })
    } finally {
      scanBtn.disabled = false
      scanBtn.textContent = '⟳ Scan'
    }
  })

  const sync = () => {
    const s = getState()
    const c = completeness()
    countTo(coinEl, s.wallet.coins)
    countTo(valueEl, s.wallet.collectionValue)
    const sealed = sealedTotal()
    const cheapest = [...s.packTypes].sort((a, b) => packCost(a) - packCost(b))[0]
    const canBuy = cheapest ? affordable(cheapest) : 0
    const badge = packsBadge(sealed, s.wallet.freePack.available)
    packsBadgeEl.textContent = badge
    packsBadgeEl.hidden = badge === ''
    packsBadgeEl.classList.toggle('is-free', sealed === 0 && s.wallet.freePack.available)
    packsLink.title = packsTitle(sealed, canBuy, s.wallet.freePack.available)
    const ch = s.wallet.collectionChange
    changeEl.textContent = valueChangeText(ch.change)
    changeEl.className = `pill-change ${ch.change > 0.0005 ? 'is-up' : ch.change < -0.0005 ? 'is-down' : ''}`
    valuePill.title = `What all your cards are worth today. ${ch.coins === 0 ? 'No change' : `${ch.coins > 0 ? 'Up' : 'Down'} ${Math.abs(ch.coins).toLocaleString()} coins`} at the last price update (prices change every ${s.wallet.market.tickHours} hours). Click for the market.`
    // The versions count to their new value like the coins do (at once with reduced motion).
    countTo(filledEl, c.versions)
    countTo(totalEl, c.versionsTotal)
    versionPill.title = pillTitle(c)
    // No cards yet: only the setup screen exists, so the nav and the collection numbers are hidden.
    root.classList.toggle('is-empty', s.mode === 'empty')
  }
  subscribe(sync)
  sync()

  // A change in versions: the delta sits next to the numbers for 2 seconds, then the pill is plain again.
  // A gain pops; a loss is a calm fade in the colour of the treatment whose slot emptied, never red.
  let deltaTimer: ReturnType<typeof setTimeout> | undefined
  onSlotsChange((change) => {
    const text = pillDeltaText(change)
    if (!text) return
    const loss = change.delta < 0
    const lost = change.emptied?.[0]?.treatment
    deltaEl.textContent = text
    deltaEl.hidden = false
    versionPill.style.setProperty('--tone', loss && lost ? visualFor(lost).color : 'var(--online)')
    versionPill.classList.remove('is-gain', 'is-loss')
    void versionPill.offsetWidth // restart the animation
    versionPill.classList.add(loss ? 'is-loss' : 'is-gain')
    if (deltaTimer !== undefined) clearTimeout(deltaTimer)
    deltaTimer = setTimeout(() => {
      deltaEl.hidden = true
      versionPill.classList.remove('is-gain', 'is-loss')
    }, PILL_DELTA_MS)
  })

  health().then((h) => {
    const label = profileBadge(h.profile)
    if (!label) return
    const badge = root.querySelector<HTMLElement>('#profile-badge')!
    badge.textContent = label
    badge.hidden = false
    setTitlePrefix(`[${h.profile}] `)
  }).catch(() => { /* badge is cosmetic */ })

  showScanProgress(root, root.querySelector<HTMLElement>('#import-bar')!)

  const syncMute = () => {
    muteBtn.textContent = isMuted() ? '🔇' : '🔊'
    muteBtn.title = isMuted() ? 'Sound off' : 'Sound on'
  }
  muteBtn.addEventListener('click', () => { setMuted(!isMuted()); syncMute() })
  window.addEventListener('mco:sound', syncMute)
  syncMute()

  return root.querySelector<HTMLElement>('#content')!
}

export function setActiveNav(id: string): void {
  document.querySelectorAll<HTMLAnchorElement>('[data-nav]').forEach((a) => {
    if (a.dataset.nav === id) a.setAttribute('aria-current', 'page')
    else a.removeAttribute('aria-current')
  })
}

/**
 * A slim progress bar while a scan runs. The page asks for progress only during a scan: one check
 * at startup (a scan may still be running from before a reload), then nothing until the player
 * starts one.
 */
function showScanProgress(root: HTMLElement, bar: HTMLElement): void {
  const text = root.querySelector<HTMLElement>('#import-text')!
  const prog = root.querySelector<HTMLProgressElement>('#import-progress')!
  onScanStatus((st) => {
    bar.hidden = !st
    if (!st) return
    text.textContent = `Scanning your folder… ${st.done.toLocaleString()} / ${st.total.toLocaleString()}`
    prog.max = Math.max(1, st.total)
    prog.value = st.done
  })
  void watchScan()
}
