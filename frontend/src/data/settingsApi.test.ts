import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from './api.ts'
import { NeedsConfirmError, setMediaFolder } from './settingsApi.ts'

const respond = (status: number, body: unknown) =>
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(body), { status })))

describe('settingsApi errors', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('maps a 409 to NeedsConfirmError with media_count', async () => {
    respond(409, { detail: { needs_confirm: true, media_count: 7 } })
    const err = await setMediaFolder('/media/x').catch((e) => e)
    expect(err).toBeInstanceOf(NeedsConfirmError)
    expect((err as NeedsConfirmError).mediaCount).toBe(7)
  })

  it('maps a 422 to ApiError with the server message', async () => {
    respond(422, { detail: { message: 'Folder does not exist' } })
    const err = await setMediaFolder('/media/nope').catch((e) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).status).toBe(422)
    expect((err as ApiError).message).toBe('Folder does not exist')
  })

  it('sends the client header on PUT', async () => {
    respond(200, {})
    await setMediaFolder('/media/x')
    const init = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0][1] as RequestInit
    expect((init.headers as Record<string, string>)['X-MCO-Client']).toBe('1')
  })
})
