/** Pure gating for tours that start by themselves the first time a page is visited. */

import type { TutorialStatus } from './progress.ts'

export interface FirstVisitInput {
  mode: 'empty' | 'live'
  progress: Map<string, TutorialStatus>
  tourActive: boolean
  id: string
  hash: string
}

export function shouldRunFirstVisit({ mode, progress, tourActive, id, hash }: FirstVisitInput): boolean {
  if (mode !== 'live' || tourActive) return false
  if (hash.startsWith('#/setup')) return false
  // Live mode means setup is behind the player (it is only skipped by leaving it), so no `setup` entry is needed.
  if (!progress.has('welcome')) return false
  return !progress.has(id)
}
