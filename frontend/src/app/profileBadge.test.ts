import { describe, expect, it } from 'vitest'
import { profileBadge } from './shell.ts'

describe('profileBadge', () => {
  it('is hidden for the main profile or when unknown', () => {
    expect(profileBadge('main')).toBeNull()
    expect(profileBadge(undefined)).toBeNull()
  })
  it('labels other profiles', () => {
    expect(profileBadge('test')).toBe('TEST · test')
  })
})
