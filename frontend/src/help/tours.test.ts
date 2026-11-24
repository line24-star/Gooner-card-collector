import { describe, expect, it } from 'vitest'
import { loadArticles } from './content.ts'
import { ONBOARDING_TOUR_ID, TOURS, findTour } from './tours.ts'

describe('tours', () => {
  it('has the onboarding tour', () => {
    expect(findTour(ONBOARDING_TOUR_ID)).toBeDefined()
  })
  it('ids are unique and valid for the backend', () => {
    const ids = TOURS.map((t) => t.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const id of ids) expect(id).toMatch(/^[a-z0-9][a-z0-9-]{0,63}$/)
  })
  it('every #tour: link in help content points at a real tour', () => {
    for (const a of loadArticles()) {
      for (const [, id] of a.body.matchAll(/\(#tour:([\w-]+)\)/g)) expect(findTour(id), `${a.id} → ${id}`).toBeDefined()
    }
  })
})
