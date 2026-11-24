/** Client for /api/tutorials/progress: which tours/tutorials are finished or skipped. */

import { withClient } from '../data/api.ts'

export type TutorialStatus = 'completed' | 'skipped'

export interface ProgressEntry {
  tutorial_id: string
  status: TutorialStatus
  updated_at: string
  /** v0.9.0: the tours pay packs, once, when the last tour that counts is completed (usually empty). */
  packs?: { pack: string; count: number }[]
  inventory?: Record<string, number> | null
}

export async function fetchProgress(): Promise<Map<string, TutorialStatus>> {
  const r = await fetch('/api/tutorials/progress')
  if (!r.ok) throw new Error(`progress fetch failed: ${r.status}`)
  const rows = (await r.json()) as ProgressEntry[]
  return new Map(rows.map((x) => [x.tutorial_id, x.status]))
}

export async function markTutorial(id: string, status: TutorialStatus): Promise<ProgressEntry> {
  const r = await fetch(`/api/tutorials/progress/${encodeURIComponent(id)}`, withClient({
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  }))
  if (!r.ok) throw new Error(`progress save failed: ${r.status}`)
  return (await r.json()) as ProgressEntry
}

export async function resetProgress(): Promise<void> {
  await fetch('/api/tutorials/progress', withClient({ method: 'DELETE' }))
}
