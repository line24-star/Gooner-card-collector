/**
 * Screen-space celebration effects for rare cards (Foil / Holo / Special).
 * One shared <canvas> + one rAF loop for all particles, plus a few CSS-animated DOM layers.
 * Never blocks clicks, never leaves elements behind (see stopCelebrations()).
 */

import './burst.css'
import { shake } from './effects'

export type RarityLevel = 0 | 1 | 2 | 3

export interface CelebrateOptions {
  level: RarityLevel
  hue?: number
  origin?: { x: number; y: number }
  host?: HTMLElement
  label?: string
  intensity?: number
}

export interface BurstPlan {
  particles: number
  durationMs: number
  rings: number
  rays: number
  rain: number // falling gold drops per second (0 = none)
  tint: number // peak opacity of the screen tint, 0..1
  shake: 0 | 1 | 2 | 3
  banner: boolean
  colors: string[]
}

const MAX_PARTICLES = 600
const MAX_BURST = 400 // one celebration never takes more than this many from the pool

const BASE: Record<RarityLevel, Omit<BurstPlan, 'colors'>> = {
  0: { particles: 0, durationMs: 0, rings: 0, rays: 0, rain: 0, tint: 0, shake: 0, banner: false },
  1: { particles: 36, durationMs: 700, rings: 0, rays: 0, rain: 0, tint: 0.12, shake: 0, banner: false },
  2: { particles: 120, durationMs: 1400, rings: 1, rays: 0, rain: 0, tint: 0.25, shake: 1, banner: true },
  3: { particles: 220, durationMs: 2600, rings: 2, rays: 1, rain: 70, tint: 0.4, shake: 3, banner: true },
}

const PALETTES: Record<RarityLevel, string[]> = {
  0: [],
  1: ['#6fd3ff', '#bfe9ff', '#ffffff', '#9ad7ff'],
  2: ['#ff7a7a', '#ffd36e', '#7affb1', '#6ec8ff', '#c08bff'],
  3: ['#ffcb47', '#ffe08a', '#fff3c4', '#ffb21f'],
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** PURE: what an effect of this level consists of. */
export function burstPlan(level: RarityLevel, hue = 230, intensity = 1): BurstPlan {
  const base = BASE[level]
  if (!base || level === 0) return { ...BASE[0], colors: [] }
  const k = clamp(Number.isFinite(intensity) ? intensity : 1, 0.5, 2)
  const h = ((Number.isFinite(hue) ? hue : 230) % 360 + 360) % 360
  return {
    ...base,
    particles: Math.min(MAX_BURST, Math.round(base.particles * k)),
    durationMs: Math.round(base.durationMs * (0.85 + 0.15 * k)),
    rain: Math.round(base.rain * k),
    colors: [...PALETTES[level], `hsl(${h} 90% 70%)`],
  }
}

// ---------------------------------------------------------------- state

const KIND_BURST = 0
const KIND_RAIN = 1

// Struct-of-arrays pool: no per-frame allocations.
const px = new Float32Array(MAX_PARTICLES)
const py = new Float32Array(MAX_PARTICLES)
const pvx = new Float32Array(MAX_PARTICLES)
const pvy = new Float32Array(MAX_PARTICLES)
const plife = new Float32Array(MAX_PARTICLES) // seconds left; <= 0 means free
const pmax = new Float32Array(MAX_PARTICLES)
const psize = new Float32Array(MAX_PARTICLES)
const pkind = new Uint8Array(MAX_PARTICLES)
const pcol = new Uint16Array(MAX_PARTICLES)
const palette: string[] = [] // colour strings, referenced by index
const PALETTE_MAX = 60

let canvas: HTMLCanvasElement | null = null
let ctx: CanvasRenderingContext2D | null = null
let cw = 0
let ch = 0
let dpr = 1
let raf = 0
let lastT = 0
let alive = 0
let rainUntil = 0 // performance.now() timestamp
let rainRate = 0
let rainAcc = 0
let rainCols: number[] = []

const layers = new Set<HTMLElement>()
const timers = new Set<ReturnType<typeof setTimeout>>()

const reduced = () =>
  typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches

function colorIndex(c: string): number {
  let i = palette.indexOf(c)
  if (i < 0) {
    if (palette.length >= PALETTE_MAX) return palette.length - 1 // keep bounded
    i = palette.push(c) - 1
  }
  return i
}

function later(fn: () => void, ms: number) {
  const id = setTimeout(() => { timers.delete(id); fn() }, ms)
  timers.add(id)
}

function addLayer(host: HTMLElement, cls: string, ms: number, vars: Record<string, string> = {}): HTMLElement {
  const el = document.createElement('div')
  el.className = `burst-layer ${cls}`
  for (const k in vars) el.style.setProperty(k, vars[k])
  el.style.setProperty('--burst-dur', `${ms}ms`)
  host.append(el)
  layers.add(el)
  later(() => { layers.delete(el); el.remove() }, ms + 80)
  return el
}

// ---------------------------------------------------------------- canvas loop

function fitCanvas() {
  if (!canvas || !ctx) return
  const w = window.innerWidth
  const h = window.innerHeight
  const d = Math.min(window.devicePixelRatio || 1, 2)
  if (w === cw && h === ch && d === dpr) return
  cw = w; ch = h; dpr = d
  canvas.width = Math.max(1, Math.round(w * d))
  canvas.height = Math.max(1, Math.round(h * d))
  ctx.setTransform(d, 0, 0, d, 0, 0)
}

function ensureCanvas(host: HTMLElement): boolean {
  if (canvas && !canvas.isConnected) teardownCanvas() // its host was removed
  if (canvas) return true
  const c = document.createElement('canvas')
  c.className = 'burst-canvas'
  const g = c.getContext('2d')
  if (!g) return false
  canvas = c
  ctx = g
  cw = ch = 0
  host.append(c)
  fitCanvas()
  return true
}

function teardownCanvas() {
  if (raf) cancelAnimationFrame(raf)
  raf = 0
  canvas?.remove()
  canvas = null
  ctx = null
  alive = 0
  plife.fill(0)
  rainUntil = 0
  rainAcc = 0
}

function spawn(x: number, y: number, vx: number, vy: number, life: number, size: number, kind: number, col: number) {
  for (let i = 0; i < MAX_PARTICLES; i++) {
    if (plife[i] <= 0) {
      px[i] = x; py[i] = y; pvx[i] = vx; pvy[i] = vy
      plife[i] = life; pmax[i] = life; psize[i] = size; pkind[i] = kind; pcol[i] = col
      alive++
      return
    }
  }
  // pool full: drop the particle
}

function startLoop() {
  if (raf || !canvas) return
  lastT = performance.now()
  raf = requestAnimationFrame(tick)
}

function tick(now: number) {
  raf = 0
  if (!canvas || !ctx) return
  const dt = Math.min(0.05, (now - lastT) / 1000)
  lastT = now
  fitCanvas()

  // rain emitter
  if (now < rainUntil && rainRate > 0) {
    rainAcc += rainRate * dt
    while (rainAcc >= 1) {
      rainAcc -= 1
      const c = rainCols[(Math.random() * rainCols.length) | 0]
      spawn(Math.random() * cw, -20, (Math.random() - 0.5) * 30, 520 + Math.random() * 420,
        (ch + 40) / 520 + 0.4, 1.5 + Math.random() * 1.5, KIND_RAIN, c)
    }
  }

  ctx.clearRect(0, 0, cw, ch)
  ctx.globalCompositeOperation = 'lighter'
  let n = 0
  const drag = Math.pow(0.06, dt) // burst particles lose most speed within ~1 s
  for (let i = 0; i < MAX_PARTICLES; i++) {
    if (plife[i] <= 0) continue
    plife[i] -= dt
    if (plife[i] <= 0 || py[i] > ch + 30) { plife[i] = 0; alive--; continue }
    n++
    const t = plife[i] / pmax[i]
    if (pkind[i] === KIND_BURST) {
      pvx[i] *= drag
      pvy[i] = pvy[i] * drag + 260 * dt
    }
    px[i] += pvx[i] * dt
    py[i] += pvy[i] * dt
    ctx.globalAlpha = pkind[i] === KIND_RAIN ? Math.min(1, t * 3) * 0.9 : Math.min(1, t * 1.6)
    ctx.fillStyle = palette[pcol[i]]
    const s = psize[i]
    if (pkind[i] === KIND_RAIN) {
      ctx.fillRect(px[i], py[i], s, s * 7)
    } else {
      const r = s * (0.4 + 0.6 * t)
      ctx.fillRect(px[i] - r, py[i] - r, r * 2, r * 2)
    }
  }
  ctx.globalAlpha = 1
  ctx.globalCompositeOperation = 'source-over'

  if (n > 0 || now < rainUntil) raf = requestAnimationFrame(tick)
  else teardownCanvas() // nothing alive: the loop and the canvas are gone
}

// ---------------------------------------------------------------- public API

function defaultHost(): HTMLElement {
  return (document.querySelector('.opening') as HTMLElement | null) ?? document.body
}

export function celebrate(opts: CelebrateOptions): void {
  const level = opts.level
  if (!level) return
  const plan = burstPlan(level, opts.hue, opts.intensity)
  if (plan.particles === 0 && plan.tint === 0) return
  const host = opts.host ?? defaultHost()
  const ms = plan.durationMs
  const tintColor = level === 1 ? '#8fd8ff' : level === 2 ? '#a070ff' : '#ffc93c'

  if (reduced()) {
    addLayer(host, 'burst-tint burst-tint-soft', 700, { '--burst-color': tintColor, '--burst-peak': String(Math.min(0.25, plan.tint)) })
    if (plan.banner && opts.label) addBanner(host, opts.label, level, 1200)
    return
  }

  const ox = opts.origin?.x ?? window.innerWidth / 2
  const oy = opts.origin?.y ?? window.innerHeight / 2
  const at = { '--burst-x': `${ox}px`, '--burst-y': `${oy}px` }

  addLayer(host, 'burst-tint', ms, { '--burst-color': tintColor, '--burst-peak': String(plan.tint) })
  if (level === 1) addLayer(host, 'burst-sweep', 400)
  for (let i = 0; i < plan.rings; i++) {
    const delay = i * 180
    addLayer(host, 'burst-ring', 900 + delay, {
      ...at, '--burst-color': level === 3 ? '#ffcb47' : '#c38bff', '--burst-delay': `${delay}ms`,
    })
  }
  for (let i = 0; i < plan.rays; i++) addLayer(host, 'burst-rays', ms, at)
  if (plan.banner && opts.label) addBanner(host, opts.label, level, Math.min(ms, 1800))
  if (plan.shake) {
    const stage = host.querySelector('.opening-stage') as HTMLElement | null
    if (stage) shake(stage, plan.shake)
  }

  if (!ensureCanvas(host)) return // no 2d context: DOM layers only
  const cols = plan.colors.map(colorIndex)
  // the pack's hue colour gets extra weight (it is the last palette entry)
  for (let i = 0; i < plan.particles; i++) {
    const a = Math.random() * Math.PI * 2
    const speed = (level === 1 ? 160 : level === 2 ? 260 : 340) * (0.35 + Math.random() * 0.9)
    const c = i % 4 === 3 ? cols[cols.length - 1] : cols[(Math.random() * cols.length) | 0]
    spawn(ox, oy, Math.cos(a) * speed, Math.sin(a) * speed - 60,
      (level === 1 ? 0.6 : level === 2 ? 1.0 : 1.4) * (0.6 + Math.random() * 0.5),
      level === 1 ? 2 + Math.random() * 2 : 3 + Math.random() * 3.5, KIND_BURST, c)
  }
  if (plan.rain > 0) {
    // gold rain falls for the part of the effect after the initial burst
    rainUntil = Math.max(rainUntil, performance.now() + ms - 500)
    rainRate = Math.min(300, rainRate + plan.rain)
    rainCols = cols.slice(0, 4)
  }
  startLoop()
}

function addBanner(host: HTMLElement, label: string, level: RarityLevel, ms: number) {
  const el = addLayer(host, `burst-banner burst-banner-${level}`, ms)
  el.textContent = label
}

/** Remove everything at once (the overlay closes). Safe to call any time. */
export function stopCelebrations(): void {
  for (const id of timers) clearTimeout(id)
  timers.clear()
  for (const el of layers) el.remove()
  layers.clear()
  teardownCanvas()
  rainRate = 0
}
