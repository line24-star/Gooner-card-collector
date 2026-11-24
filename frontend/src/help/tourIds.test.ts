import { describe, expect, it } from 'vitest'
import toml from '../../../config/economy.toml?raw'
import { REWARDED_TOUR_IDS, TOURS } from './tours.ts'

describe('rewarded tour ids', () => {
  it('match the server allowlist in config/economy.toml', () => {
    const m = toml.match(/^tours\s*=\s*\[([^\]]*)\]/m)!
    const server = [...m[1].matchAll(/"([^"]+)"/g)].map((x) => x[1]).sort()
    expect([...REWARDED_TOUR_IDS].sort()).toEqual(server)
  })
  it('every rewarded id is a real tour', () => {
    const ids = TOURS.map((t) => t.id)
    for (const id of REWARDED_TOUR_IDS) expect(ids).toContain(id)
  })
  it('has a packs tour on the packs page', () => {
    expect(TOURS.find((t) => t.id === 'packs')?.page).toBe('packs')
  })
})
