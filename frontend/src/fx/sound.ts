/**
 * Sound effects via Web Audio. All effects are SYNTHESIZED placeholders so the app has
 * audio feedback with zero asset files; swap any entry for a decoded sample later
 * (keep the same keys). The AudioContext is created lazily on first use after a gesture.
 *
 * Every effect is a list of notes (data, not code), so its length can be read without playing it:
 * `sfxDuration`. The rules of the Anticipation and Reveal Feedback Spec, section 6, are tested on
 * that data (fx/sound.test.ts): every sound is shorter than 800 ms, and the slot-fill sound is not
 * the generic reveal sound and not a high-rarity one. One global mute switches all of it off; it
 * is remembered across sessions.
 */

export type Sfx =
  | 'click' | 'coin' | 'crack' | 'flip' | 'near-miss'
  | 'reveal-0' | 'reveal-1' | 'reveal-2' | 'reveal-3' | 'slot-fill'

/** One voice of an effect. Times in seconds. A tone slides to `slideTo` Hz when that is given. */
export type Note =
  | { kind: 'tone'; freq: number; start: number; dur: number; wave: OscillatorType; gain: number; slideTo?: number }
  | { kind: 'noise'; start: number; dur: number; gain: number }

/** No sound may be as long as this (spec 6: "all stings are short"). */
export const MAX_SFX_MS = 800

const MUTE_KEY = 'mco.muted'
let ctx: AudioContext | null = null
let muted = (() => {
  try { return localStorage.getItem(MUTE_KEY) === '1' } catch { return false }
})()

export function isMuted(): boolean { return muted }

/** The one mute for every sound of the app. Remembered in this browser. */
export function setMuted(m: boolean): void {
  muted = m
  try { localStorage.setItem(MUTE_KEY, m ? '1' : '0') } catch { /* private mode */ }
}

function ac(): AudioContext | null {
  if (muted) return null
  ctx ??= new AudioContext()
  if (ctx.state === 'suspended') void ctx.resume()
  return ctx
}

const t = (freq: number, start: number, dur: number, wave: OscillatorType = 'sine', gain = 0.15, slideTo?: number): Note =>
  ({ kind: 'tone', freq, start, dur, wave, gain, slideTo })
const n = (start: number, dur: number, gain = 0.2): Note => ({ kind: 'noise', start, dur, gain })

const ARP = [523.25, 659.25, 783.99, 1046.5, 1318.5]

export const EFFECTS: Record<Sfx, Note[]> = {
  click: [t(900, 0, 0.05, 'square', 0.04)],
  coin: [t(988, 0, 0.08, 'square', 0.06), t(1319, 0.07, 0.18, 'square', 0.06)],
  crack: [n(0, 0.35, 0.25), t(140, 0, 0.25, 'sawtooth', 0.08, 60)],
  flip: [n(0, 0.08, 0.12)],
  'near-miss': [t(700, 0, 0.35, 'triangle', 0.08, 520)],
  // The generic reveal: one short triangle note.
  'reveal-0': [t(440, 0, 0.12, 'triangle', 0.08)],
  'reveal-1': ARP.slice(0, 2).map((f, i) => t(f, i * 0.07, 0.25, 'triangle', 0.1)),
  // The high-rarity stings: rising triangle arpeggios, the Special with a held top note and a hiss.
  'reveal-2': ARP.slice(0, 4).map((f, i) => t(f, i * 0.07, 0.35, 'triangle', 0.11)),
  'reveal-3': [
    ...ARP.map((f, i) => t(f, i * 0.06, 0.4, 'triangle', 0.12)),
    t(1046.5, 0.3, 0.45, 'sine', 0.1),
    n(0.3, 0.4, 0.08),
  ],
  // A version slot filled: three soft bell notes (a fifth, then the octave). Sine waves on other
  // pitches than the arpeggios, so it never reads as a reveal sting.
  'slot-fill': [t(587.33, 0, 0.16, 'sine', 0.13), t(880, 0.09, 0.2, 'sine', 0.13), t(1174.66, 0.19, 0.42, 'sine', 0.11)],
}

/** The tail an oscillator is given after its note before it is stopped. */
const STOP_TAIL = 0.02

/** How long an effect sounds, in ms: the end of its last voice. */
export function sfxDuration(sfx: Sfx): number {
  return Math.round(Math.max(0, ...EFFECTS[sfx].map((v) => v.start + v.dur + (v.kind === 'tone' ? STOP_TAIL : 0))) * 1000)
}

/** What makes two effects sound different: the wave and pitch of each tone, in order. */
export function sfxSignature(sfx: Sfx): string {
  return EFFECTS[sfx].map((v) => (v.kind === 'tone' ? `${v.wave}:${Math.round(v.freq)}` : 'noise')).join(' ')
}

function tone(a: AudioContext, v: Extract<Note, { kind: 'tone' }>) {
  const at = a.currentTime + v.start
  const osc = a.createOscillator()
  const g = a.createGain()
  osc.type = v.wave
  osc.frequency.setValueAtTime(v.freq, at)
  if (v.slideTo) osc.frequency.exponentialRampToValueAtTime(v.slideTo, at + v.dur)
  g.gain.setValueAtTime(0.0001, at)
  g.gain.exponentialRampToValueAtTime(v.gain, at + 0.01)
  g.gain.exponentialRampToValueAtTime(0.0001, at + v.dur)
  osc.connect(g).connect(a.destination)
  osc.start(at)
  osc.stop(at + v.dur + STOP_TAIL)
}

function noise(a: AudioContext, v: Extract<Note, { kind: 'noise' }>) {
  const buf = a.createBuffer(1, Math.floor(a.sampleRate * v.dur), a.sampleRate)
  const data = buf.getChannelData(0)
  for (let i = 0; i < data.length; i++) data[i] = (Math.random() * 2 - 1) * (1 - i / data.length)
  const src = a.createBufferSource()
  const g = a.createGain()
  const f = a.createBiquadFilter()
  f.type = 'highpass'
  f.frequency.value = 1200
  g.gain.value = v.gain
  src.buffer = buf
  src.connect(f).connect(g).connect(a.destination)
  src.start(a.currentTime + v.start)
}

export function play(sfx: Sfx): void {
  try {
    const a = ac() // null while muted: nothing is created, nothing sounds
    if (!a) return
    for (const v of EFFECTS[sfx]) { if (v.kind === 'tone') tone(a, v); else noise(a, v) }
  } catch { /* audio unavailable */ }
}
