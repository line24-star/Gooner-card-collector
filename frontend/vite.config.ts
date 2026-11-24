import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'

// Single source of truth for the app version: /version.json (see README → Versioning).
const { stage, version } = JSON.parse(
  readFileSync(new URL('../version.json', import.meta.url), 'utf-8'),
) as { stage: string; version: string }

const frontendPort = Number(process.env.MCO_FRONTEND_PORT ?? 5173)
const backendPort = Number(process.env.MCO_BACKEND_PORT ?? 8000)

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(version),
    __APP_STAGE__: JSON.stringify(stage),
  },
  server: {
    port: frontendPort,
    proxy: {
      // ws: true also proxies the /api/ws/presence WebSocket
      '/api': { target: `http://127.0.0.1:${backendPort}`, ws: true },
    },
  },
})
