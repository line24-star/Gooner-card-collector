/**
 * Toasts: short messages that slide in at the bottom right and leave by themselves.
 * They never block the page. Inside a modal dialog or the pack overlay they are added to that
 * layer, because a modal sits above everything in <body>.
 */

import './toast.css'
import { rewardText } from '../data/economyApi.ts'

export type ToastKind = 'info' | 'coin' | 'good' | 'warn'

export interface ToastOptions {
  kind?: ToastKind
  /** Milliseconds on screen (default 4200). */
  ms?: number
  /** A link shown after the text, e.g. "Open a pack". */
  action?: { label: string; href: string }
}

function host(): HTMLElement {
  const layer = [...document.querySelectorAll<HTMLElement>('dialog[open]')].pop() ?? document.querySelector<HTMLElement>('.opening') ?? document.body
  let box = layer.querySelector<HTMLElement>(':scope > .toasts')
  if (!box) {
    box = document.createElement('div')
    box.className = 'toasts'
    box.setAttribute('role', 'status')
    box.setAttribute('aria-live', 'polite')
    layer.append(box)
  }
  return box
}

export function toast(text: string, opts: ToastOptions = {}): HTMLElement {
  const el = document.createElement('div')
  el.className = `toast toast-${opts.kind ?? 'info'}`
  const span = document.createElement('span')
  span.textContent = text
  el.append(span)
  if (opts.action) {
    const a = document.createElement('a')
    a.href = opts.action.href
    a.textContent = opts.action.label
    el.append(a)
  }
  const box = host()
  box.append(el)
  while (box.children.length > 4) box.firstElementChild?.remove() // never a wall of toasts
  const leave = () => {
    el.classList.add('is-leaving')
    setTimeout(() => el.remove(), 250)
  }
  setTimeout(leave, opts.ms ?? 4200)
  el.addEventListener('click', (e) => { if (!(e.target as HTMLElement).closest('a')) leave() })
  return el
}

/** "Milestone 5% reached: 1 Standard pack to claim". */
export function milestoneReachedText(m: { at: number; reward: { pack: string; count: number }[] }): string {
  const pct = (m.at * 100).toLocaleString('en-US', { maximumFractionDigits: 1 })
  const packs = rewardText(m.reward)
  return `Milestone ${pct}% reached${packs ? `: ${packs} to claim` : ''}`
}

/** One line for each milestone a server action just reached. v0.9.0: nothing is paid by itself,
 * the packs wait to be claimed on the Stats page. */
export function toastMilestones(reached: unknown[] | undefined): void {
  for (const m of reached ?? []) {
    if (m && typeof m === 'object' && typeof (m as { at?: unknown }).at === 'number') {
      const r = m as { at: number; reward?: { pack: string; count: number }[] }
      toast(milestoneReachedText({ at: r.at, reward: r.reward ?? [] }), { kind: 'good', action: { label: 'Claim', href: '#/home' } })
    }
  }
}
