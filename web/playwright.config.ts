import { defineConfig } from '@playwright/test'

// Starts its own `poker serve` (on a spare port, with a throwaway runs dir)
// against the built UI. Run `npm run build` first.
const PORT = 8777
const RUNS = `${process.env.TMPDIR ?? '/tmp'}/poker-e2e-runs-${Date.now()}`

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  use: { baseURL: `http://127.0.0.1:${PORT}`, viewport: { width: 1440, height: 860 } },
  webServer: {
    command: `../.venv/bin/poker serve --port ${PORT} --out ${RUNS}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
})
