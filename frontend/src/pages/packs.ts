/**
 * Packs: your sealed packs on top (the inventory you open from), the shop below it.
 *
 * Buying a pack does not open it: it goes into the inventory, sealed. The shop shows three packs
 * on a rarity ladder (better odds cost more) with today's real prices, how many the balance buys
 * and each pack's own odds. When coins run short a tile says how many are missing and links to
 * the ways to earn them.
 */

import { esc } from '../cards/card.ts'
import { visualFor } from '../cards/treatments.ts'
import { ApiError } from '../data/api.ts'
import { affordable, buyPacks, claimFreePack, getState, packCost, refreshWallet, sealedTotal, subscribe } from '../data/store.ts'
import { oneIn, packOdds, pct, perPack } from '../data/packOdds.ts'
import type { PackType } from '../data/types.ts'
import { floatCoins } from '../fx/effects.ts'
import { play } from '../fx/sound.ts'
import { openFromInventory, sealedOf } from '../packs/flow.ts'
import { packArtHTML } from '../packs/opening.ts'
import { firstVisitTour } from '../help/index.ts'
import { countdownText, startCountdowns } from '../ui/countdown.ts'
import { overlayOpen } from '../ui/overlay.ts'
import { toast } from '../ui/toast.ts'
import type { Page } from '../app/router.ts'
import { freePackText } from './chase.ts'

// v0.9.0 (decision D4): every pack has the same odds. The soft-pity line and bar are gone.

// The odds helpers live in data/packOdds.ts (the card panel uses them too).
export { oneIn, packOdds, pct, perPack }

/** What the player typed in "Buy X", brought into range: at least 1, at most what the coins buy (0 when they buy none). */
export function clampBuy(typed: string | number, max: number): number {
  const n = Math.floor(Number(typed))
  if (max < 1) return 0
  if (!Number.isFinite(n) || n < 1) return 1
  return Math.min(n, max)
}

/**
 * "3 cards are only in Deluxe and Premium Packs · 2 still to pull", or '' when this pack holds
 * nothing the cheaper packs lack. The scarcest cards are not in the cheap packs.
 */
export function exclusiveText(
  pack: { id: string; name: string; cost: number }, packs: { id: string; name: string; cost: number }[],
  exclusives: Record<string, { cards: number; missing: number }>,
): string {
  const own = exclusives[pack.id]
  if (!own || own.cards < 1) return ''
  const names = packs.filter((p) => p.cost >= pack.cost).sort((a, b) => a.cost - b.cost).map((p) => p.name.replace(/ Pack$/, ''))
  const where = names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]} Packs` : `${names[0]} Packs`
  const left = own.missing > 0 ? ` · ${own.missing} still to pull` : ' · you have them all'
  return `${own.cards} ${own.cards === 1 ? 'card is' : 'cards are'} only in ${where}${left}`
}

/** How many packs "Open all" opens in one go. */
export const OPEN_ALL_MAX = 10

export const packsPage: Page = {
  id: 'packs',
  title: 'Packs',
  icon: '✦',
  mount(el) {
    const wanted = new URLSearchParams(location.hash.split('?')[1] ?? '').get('pack')
    let busy = false
    const typed = new Map<string, string>() // what is in each "Buy X" field, kept across re-renders
    let stopCountdowns = () => {}

    function inventoryHTML(): string {
      const s = getState()
      const owned = s.packTypes.filter((p) => sealedOf(p.id) > 0)
      const f = s.wallet.freePack
      const free = freePackText(f)
      return `
        <section class="inv" data-tour="pack-inventory" aria-label="Your packs">
          <div class="inv-head">
            <h2>Your packs</h2>
            <span class="muted">${sealedTotal() ? `${sealedTotal()} sealed` : 'None sealed'}</span>
          </div>
          <div class="inv-row">
            ${owned.map((p) => {
              const n = sealedOf(p.id)
              return `
              <article class="inv-item" data-pack="${esc(p.id)}">
                <button class="inv-art" type="button" data-open="1" aria-label="Open a ${esc(p.name)}">${packArtHTML(p, 'pack-float')}<span class="inv-count">×${n}</span></button>
                <div class="inv-buttons">
                  <button class="btn${n >= OPEN_ALL_MAX ? ' btn-ghost' : ''}" data-open="1">Open</button>
                  ${n > 1 ? `<button class="btn${n >= OPEN_ALL_MAX ? '' : ' btn-ghost'}" data-open="${Math.min(n, OPEN_ALL_MAX)}">Open ${Math.min(n, OPEN_ALL_MAX)}</button>` : ''}
                </div>
              </article>`
            }).join('')}
            ${owned.length ? '' : '<p class="inv-empty muted">You have no sealed packs. Buy one below, or claim your free pack.</p>'}
            ${free ? `<div class="inv-free ${f.available ? 'is-ready' : ''}" data-tour="pack-free">
              <span aria-hidden="true" class="inv-free-icon">🎁</span>
              <b>${esc(free.title)}</b>
              <span class="muted">${esc(free.note)}</span>
              ${f.available ? '<button class="btn" type="button" data-free>Claim</button>' : f.needsCheckin ? '<a class="btn btn-ghost" href="#/home">Check in</a>' : ''}
            </div>` : ''}
          </div>
        </section>`
    }

    function tileHTML(p: PackType, i: number): string {
      const s = getState()
      const cost = packCost(p)
      const n = affordable(p)
      const max = s.wallet.packMax[p.id] ?? n // the server counts the rising price per pack
      const short = cost - s.wallet.coins
      const shiny = s.treatments.filter((t) => t.tier > 0)
      return `
        <article class="shop-item ${p.id === wanted ? 'is-featured' : ''}" data-pack="${esc(p.id)}"${i === 0 ? ' data-tour="pack-tile"' : ''}>
          <div class="shop-art">${packArtHTML(p)}</div>
          <h3>${esc(p.name)}</h3>
          <p class="muted">${esc(p.blurb)}</p>
          ${exclusiveText(p, s.packTypes, s.wallet.packExclusives) ? `<p class="shop-exclusive" title="The scarcest cards are not in the cheaper packs">★ ${esc(exclusiveText(p, s.packTypes, s.wallet.packExclusives))}</p>` : ''}
          <ul class="shop-odds" aria-label="Odds per pack">
            ${shiny.map((t) => `<li style="--rarity:${visualFor(t.id).color}"><span>${esc(t.name)}</span><b>${oneIn(perPack(p, t))}</b></li>`).join('')}
          </ul>
          <div class="shop-price">🪙 <b>${cost.toLocaleString()}</b> <span class="muted">· ${p.size} cards</span></div>
          ${n
            ? `<p class="shop-afford">You can afford ×${n}</p>`
            : `<p class="shop-afford is-short">${short.toLocaleString()} coins short ·
                 <a href="#/organize">tag cards</a> or <a href="#/market">sell cards on the market</a></p>`}
          <div class="shop-buttons"${i === 0 ? ' data-tour="pack-open"' : ''}>
            <button class="btn" data-buy="1" ${max < 1 ? 'disabled' : ''}>Buy 1</button>
            <button class="btn btn-ghost" data-buy="10" ${max < 10 ? 'disabled' : ''}>Buy 10</button>
          </div>
          <div class="shop-buyx" title="Type how many you want. It can't go above what your coins buy.">
            <input class="input" type="number" inputmode="numeric" min="1" max="${Math.max(1, max)}" step="1"
                   value="${clampBuy(typed.get(p.id) ?? Math.min(5, max), max) || ''}" data-buy-n aria-label="How many ${esc(p.name)}s to buy" ${max < 1 ? 'disabled' : ''}>
            <button class="btn btn-ghost" data-buy="x" ${max < 1 ? 'disabled' : ''}>Buy</button>
            <span class="muted">max ${max}</span>
          </div>
        </article>`
    }

    /** The deal of this window: one pack, cheaper, a few times, with the clock running. */
    function dealHTML(): string {
      const s = getState()
      const d = s.wallet.deal
      const pack = d ? s.packTypes.find((p) => p.id === d.pack) : null
      if (!d || !pack || d.left < 1) return ''
      const can = s.wallet.coins >= d.price
      return `<div class="shop-deal" data-tour="pack-deal">
        <span class="shop-deal-tag">−${Math.round(d.discount * 100)}%</span>
        <span><b>Deal: ${esc(pack.name)} for 🪙 ${d.price.toLocaleString()}</b>
          <span class="muted">instead of ${d.fullPrice.toLocaleString()} · ${d.left} left · ends in <b data-countdown="${esc(d.endsAt)}">${countdownText(d.endsAt)}</b></span></span>
        <button class="btn" type="button" data-deal="${esc(pack.id)}" ${can ? '' : 'disabled'}>${can ? 'Buy at this price' : `${(d.price - s.wallet.coins).toLocaleString()} coins short`}</button>
      </div>`
    }

    function render() {
      const s = getState()
      el.innerHTML = `
        ${inventoryHTML()}
        <div class="shop-head">
          <h2>Pack Shop</h2>
          <p class="muted">A bought pack goes into your packs, sealed. Every pack pulls from all your photos and videos; better odds of a rare treatment cost more.</p>
        </div>
        ${dealHTML()}
        <div class="shop-grid">${s.packTypes.map(tileHTML).join('')}</div>
        <details class="rates" data-tour="pack-rates">
          <summary>Odds per card, for every pack</summary>
          <table>
            <thead><tr><th>Treatment</th>${s.packTypes.map((p) => `<th>${esc(p.name)}</th>`).join('')}<th>Coin multiplier</th></tr></thead>
            <tbody>${s.treatments.map((t) => `<tr><td>${esc(t.name)}</td>${s.packTypes.map((p) => `<td>${pct(packOdds(p, t))}</td>`).join('')}<td>×${t.multiplier}</td></tr>`).join('')}</tbody>
          </table>
          <p class="muted">The tiles show the chance per pack; this table shows the chance for each single card.
            The odds are the same for every pack you open: no pack is luckier or unluckier than the one before.
            Which card you pull follows its print run: scarce cards turn up less often.</p>
        </details>`
      stopCountdowns()
      stopCountdowns = startCountdowns(el, () => void refreshWallet().catch(() => {})) // the deal ended: fetch the next one
    }

    async function buy(btn: HTMLButtonElement, deal = false) {
      if (busy) return
      const packId = deal ? btn.dataset.deal! : btn.closest<HTMLElement>('[data-pack]')!.dataset.pack!
      const pack = getState().packTypes.find((p) => p.id === packId)!
      const max = getState().wallet.packMax[packId] ?? 0
      const field = btn.closest('[data-pack]')?.querySelector<HTMLInputElement>('[data-buy-n]')
      const count = deal ? 1 : btn.dataset.buy === 'x' ? clampBuy(field?.value ?? '1', max) : Number(btn.dataset.buy)
      if (count < 1) return
      busy = true
      btn.disabled = true
      try {
        const r = await buyPacks(packId, count, deal)
        if (r && r.bought > 0) {
          play('coin')
          floatCoins(-r.cost, document.querySelector('[data-tour="coins"]') ?? btn)
          toast(`${r.bought} ${pack.name}${r.bought > 1 ? 's' : ''} added to your packs`, { kind: 'good' })
        }
      } catch (err) {
        toast(err instanceof ApiError && err.status === 402 ? 'Not enough coins for that many packs' : err instanceof Error ? err.message : 'Could not buy the pack. Try again.', { kind: 'warn' })
      } finally {
        busy = false
        if (el.isConnected) render()
      }
    }

    el.addEventListener('click', async (e) => {
      const t = e.target as HTMLElement
      if (overlayOpen()) return
      if (t.closest('[data-free]')) {
        try {
          await claimFreePack()
          play('coin')
          toast('Your free pack is in your packs, sealed', { kind: 'good' })
        } catch (err) {
          toast(err instanceof Error ? err.message : 'Could not claim the free pack.', { kind: 'warn' })
        }
        return render()
      }
      const dealBtn = t.closest<HTMLButtonElement>('[data-deal]')
      if (dealBtn && !dealBtn.disabled) return void buy(dealBtn, true)
      const buyBtn = t.closest<HTMLButtonElement>('[data-buy]')
      if (buyBtn && !buyBtn.disabled) return void buy(buyBtn)
      const openBtn = t.closest<HTMLButtonElement>('[data-open]')
      if (!openBtn || openBtn.disabled) return
      const packId = openBtn.closest<HTMLElement>('[data-pack]')!.dataset.pack!
      const pack = getState().packTypes.find((p) => p.id === packId)!
      play('click')
      openFromInventory(pack, Number(openBtn.dataset.open), () => { if (el.isConnected) render() })
    })

    // "Buy X": the number can't go above what the coins buy. It is corrected when the player leaves the field.
    el.addEventListener('input', (e) => {
      const field = (e.target as HTMLElement).closest<HTMLInputElement>('[data-buy-n]')
      if (!field) return
      typed.set(field.closest<HTMLElement>('[data-pack]')!.dataset.pack!, field.value)
    })
    el.addEventListener('change', (e) => {
      const field = (e.target as HTMLElement).closest<HTMLInputElement>('[data-buy-n]')
      if (!field) return
      const packId = field.closest<HTMLElement>('[data-pack]')!.dataset.pack!
      const fixed = clampBuy(field.value, getState().wallet.packMax[packId] ?? 0)
      field.value = fixed ? String(fixed) : ''
      typed.set(packId, field.value)
    })
    el.addEventListener('keydown', (e) => {
      const field = (e.target as HTMLElement).closest<HTMLInputElement>('[data-buy-n]')
      if (field && e.key === 'Enter') field.closest('[data-pack]')?.querySelector<HTMLButtonElement>('[data-buy="x"]')?.click()
    })

    // Prices, the balance and the inventory change while the page is open (not while a number is being typed).
    const off = subscribe(() => {
      if (!el.isConnected || overlayOpen() || busy) return
      if (document.activeElement instanceof HTMLInputElement && el.contains(document.activeElement)) return
      render()
    })
    render()
    void firstVisitTour('packs')
    return () => { off(); stopCountdowns() }
  },
}
