import { expect, it } from 'vitest'
import { APP_STAGE, APP_VERSION, VERSION_LABEL } from './version.ts'

it('builds the label from version.json', () => {
  expect(APP_VERSION).toMatch(/^\d+\.\d+\.\d+$/)
  expect(VERSION_LABEL).toBe(`${APP_STAGE} v${APP_VERSION}`)
})
