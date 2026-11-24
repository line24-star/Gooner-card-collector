/** Client for /api/settings (media folder) and /api/onboarding. Mutating calls go through withClient. */

import { ApiError, withClient } from './api.ts'

export interface MediaFolderInfo {
  path: string
  default: string
  overridden: boolean
  exists: boolean
  media_count: number
  /** A saved folder that isn't in use (missing or unreadable), or null. */
  saved_unavailable: string | null
}

/** 409 from PUT/DELETE: the collection already has cards, so the player must confirm the switch. */
export class NeedsConfirmError extends Error {
  mediaCount: number
  constructor(mediaCount: number) {
    super('Changing the folder needs confirmation')
    this.name = 'NeedsConfirmError'
    this.mediaCount = mediaCount
  }
}

// TODO: fold this into api.ts (export its `request`) once that file is free to edit.
async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, withClient(init))
  if (!r.ok) {
    let detail: unknown = r.statusText
    try { detail = ((await r.json()) as { detail?: unknown }).detail ?? detail } catch { /* not JSON */ }
    if (r.status === 409 && detail && typeof detail === 'object' && (detail as { needs_confirm?: unknown }).needs_confirm) {
      throw new NeedsConfirmError(Number((detail as { media_count?: unknown }).media_count) || 0)
    }
    throw new ApiError(r.status, detail)
  }
  if (r.status === 204) return undefined as T
  return (await r.json()) as T
}

export const getMediaFolder = () => call<MediaFolderInfo>('/api/settings/media-folder')

export const setMediaFolder = (path: string, confirm = false) =>
  call<MediaFolderInfo>('/api/settings/media-folder', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, confirm }),
  })

export const resetMediaFolder = (confirm = false) =>
  call<MediaFolderInfo>(`/api/settings/media-folder?confirm=${confirm ? 'true' : 'false'}`, { method: 'DELETE' })

export const openMediaFolder = () => call<void>('/api/settings/media-folder/open', { method: 'POST' })

/** Shows the system "Select folder" dialog on this computer. Resolves the chosen path, or null when cancelled. */
export const pickMediaFolder = async (): Promise<string | null> =>
  (await call<{ path: string | null }>('/api/settings/media-folder/pick', { method: 'POST' })).path

/** Has the player dismissed the getting-started checklist? Falsy on any failure (don't block startup). */
export async function fetchOnboardingDismissed(): Promise<boolean> {
  try {
    const r = await fetch('/api/onboarding')
    if (!r.ok) return false
    return Boolean(((await r.json()) as { dismissed?: unknown }).dismissed)
  } catch {
    return false
  }
}
