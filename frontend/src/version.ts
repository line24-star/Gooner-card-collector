/** App version, injected at build time from /version.json by vite.config.ts. */

declare const __APP_VERSION__: string
declare const __APP_STAGE__: string

export const APP_VERSION = __APP_VERSION__
export const APP_STAGE = __APP_STAGE__
/** e.g. "alpha v0.1.0" */
export const VERSION_LABEL = `${APP_STAGE} v${APP_VERSION}`.trim()
