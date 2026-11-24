/** Anticipation and Reveal Feedback Spec, section 6: the audio rules, checked on the sounds' own data. */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { EFFECTS, isMuted, MAX_SFX_MS, play, setMuted, sfxDuration, sfxSignature, type Sfx } from './sound.ts'

const ALL = Object.keys(EFFECTS) as Sfx[]

afterEach(() => { vi.unstubAllGlobals(); setMuted(false) })

describe('sound lengths (A8)', () => {
  it('every sound is shorter than 800 ms', () => {
    expect(MAX_SFX_MS).toBe(800)
    for (const sfx of ALL) {
      expect(sfxDuration(sfx), sfx).toBeGreaterThan(0)
      expect(sfxDuration(sfx), sfx).toBeLessThan(MAX_SFX_MS)
    }
  })
  it('the length is the end of the last voice', () => {
    expect(sfxDuration('reveal-0')).toBe(140) // 0.12 s and the 20 ms tail
    expect(sfxDuration('flip')).toBe(80)
    expect(sfxDuration('reveal-3')).toBe(770)
  })
  it('the stings grow with the rarity', () => {
    expect(sfxDuration('reveal-0')).toBeLessThan(sfxDuration('reveal-1'))
    expect(sfxDuration('reveal-1')).toBeLessThan(sfxDuration('reveal-2'))
    expect(sfxDuration('reveal-2')).toBeLessThan(sfxDuration('reveal-3'))
  })
})

describe('the slot-fill sound is its own (A8)', () => {
  it('differs from the generic reveal and from the high-rarity stings', () => {
    const fill = sfxSignature('slot-fill')
    for (const other of ['reveal-0', 'reveal-1', 'reveal-2', 'reveal-3'] as Sfx[]) expect(fill, other).not.toBe(sfxSignature(other))
  })
  it('shares no wave and no pitch with any reveal sting', () => {
    const tones = (sfx: Sfx) => EFFECTS[sfx].flatMap((v) => (v.kind === 'tone' ? [v] : []))
    const fill = tones('slot-fill')
    expect(fill.length).toBeGreaterThan(1)
    expect(new Set(fill.map((v) => v.wave))).toEqual(new Set(['sine']))
    for (const other of ['reveal-0', 'reveal-1', 'reveal-2'] as Sfx[]) {
      expect(tones(other).every((v) => v.wave !== 'sine'), other).toBe(true)
    }
    const revealPitches = new Set((['reveal-0', 'reveal-1', 'reveal-2', 'reveal-3'] as Sfx[]).flatMap((s) => tones(s).map((v) => Math.round(v.freq))))
    for (const v of fill) expect(revealPitches.has(Math.round(v.freq))).toBe(false)
  })
})

describe('the global mute (A8)', () => {
  function stubAudio() {
    const made: unknown[] = []
    class FakeContext {
      currentTime = 0
      sampleRate = 8000
      state = 'running'
      destination = {}
      constructor() { made.push(this) }
      private node() {
        const node: Record<string, unknown> = {
          type: '', frequency: { value: 0, setValueAtTime() {}, exponentialRampToValueAtTime() {} },
          gain: { value: 0, setValueAtTime() {}, exponentialRampToValueAtTime() {} },
          start() {}, stop() {}, buffer: null,
        }
        node.connect = () => node
        return node
      }
      createOscillator() { return this.node() }
      createGain() { return this.node() }
      createBiquadFilter() { return this.node() }
      createBufferSource() { return this.node() }
      createBuffer(_c: number, len: number) { return { getChannelData: () => new Float32Array(len) } }
      resume() { return Promise.resolve() }
    }
    vi.stubGlobal('AudioContext', FakeContext)
    return made
  }

  it('is remembered', () => {
    const saved = new Map<string, string>()
    vi.stubGlobal('localStorage', { getItem: (k: string) => saved.get(k) ?? null, setItem: (k: string, v: string) => void saved.set(k, v) })
    setMuted(true)
    expect(isMuted()).toBe(true)
    expect(saved.get('mco.muted')).toBe('1')
    setMuted(false)
    expect(saved.get('mco.muted')).toBe('0')
  })
  it('silences every sound: nothing is even created while muted', () => {
    const made = stubAudio()
    setMuted(true)
    for (const sfx of ALL) play(sfx)
    expect(made).toHaveLength(0)
    setMuted(false)
    play('slot-fill')
    expect(made).toHaveLength(1)
  })
})
