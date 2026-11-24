/** Visual feedback: floating coin numbers, screen flash, screen shake, count-up numbers. */

import './fx.css'

const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches

export function floatText(text: string, at: { x: number; y: number }, className = 'fx-coin', host: Element = document.body): void {
  const el = document.createElement('div')
  el.className = `fx-float ${className}`
  el.textContent = text
  el.style.left = `${at.x}px`
  el.style.top = `${at.y}px`
  host.append(el)
  el.addEventListener('animationend', () => el.remove())
}

export function floatCoins(amount: number, from: Element | { x: number; y: number }, host?: Element): void {
  const at = from instanceof Element
    ? (() => { const r = from.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top } })()
    : from
  floatText(`${amount > 0 ? '+' : ''}${amount}`, at, amount >= 0 ? 'fx-coin' : 'fx-coin fx-neg', host)
}

/**
 * Coins fly out of a point and fall away: `count` of them, thrown further the higher `strength`
 * (0 to 3) is. Nothing with reduced motion or without the Web Animations API.
 */
export function coinBurst(at: { x: number; y: number }, count: number, strength = 1, host: Element = document.body): void {
  if (count < 1 || reduced()) return
  const reach = 40 + 34 * strength
  for (let i = 0; i < count; i++) {
    const el = document.createElement('span')
    el.className = 'fx-coinbit'
    el.textContent = '🪙'
    el.style.left = `${at.x}px`
    el.style.top = `${at.y}px`
    el.style.fontSize = `${13 + Math.random() * (5 + 3 * strength)}px`
    if (typeof el.animate !== 'function') return
    host.append(el)
    const a = -Math.PI / 2 + (Math.random() - 0.5) * Math.PI * 1.5 // mostly upwards
    const d = reach * (0.45 + Math.random() * 0.75)
    const dx = Math.cos(a) * d
    const dy = Math.sin(a) * d
    const anim = el.animate([
      { transform: 'translate(-50%, -50%) scale(0.5)', opacity: 0 },
      { transform: `translate(calc(-50% + ${dx * 0.7}px), calc(-50% + ${dy}px)) scale(1)`, opacity: 1, offset: 0.35 },
      { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy + reach * 0.9}px)) scale(0.8) rotate(${Math.round((Math.random() - 0.5) * 240)}deg)`, opacity: 0 },
    ], { duration: 650 + 160 * strength + Math.random() * 250, delay: Math.random() * (60 + 60 * strength), easing: 'cubic-bezier(0.2, 0.7, 0.4, 1)', fill: 'both' })
    anim.onfinish = anim.oncancel = () => el.remove()
  }
}

/** `host`: where to add the flash. Pass the open modal dialog so the flash shows above it. */
export function flash(color = 'white', strength = 0.6, host: Element = document.body): void {
  if (reduced()) return
  const el = document.createElement('div')
  el.className = 'fx-flash'
  el.style.background = color
  el.style.setProperty('--strength', String(strength))
  host.append(el)
  el.addEventListener('animationend', () => el.remove())
}

export function shake(target: HTMLElement = document.body, intensity: 1 | 2 | 3 = 1): void {
  if (reduced()) return
  target.classList.remove('fx-shake-1', 'fx-shake-2', 'fx-shake-3')
  void target.offsetWidth // restart animation
  target.classList.add(`fx-shake-${intensity}`)
  target.addEventListener('animationend', () => target.classList.remove(`fx-shake-${intensity}`), { once: true })
}

/** Animate a number in an element from its current value to `to`. */
export function countTo(el: HTMLElement, to: number, ms = 600): void {
  const from = Number(el.dataset.value ?? el.textContent?.replace(/\D/g, '') ?? 0) || 0
  el.dataset.value = String(to)
  if (reduced() || from === to) { el.textContent = to.toLocaleString(); return }
  const start = performance.now()
  const step = (now: number) => {
    const t = Math.min(1, Math.max(0, (now - start) / ms)) // a frame's time can lie just before `start`: never count backwards ("-0")
    const eased = 1 - Math.pow(1 - t, 3)
    el.textContent = (Math.round(from + (to - from) * eased) || 0).toLocaleString()
    if (t < 1) requestAnimationFrame(step)
  }
  requestAnimationFrame(step)
}
