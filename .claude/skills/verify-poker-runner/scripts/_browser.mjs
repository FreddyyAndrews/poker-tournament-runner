// Shared Playwright setup for the verify helpers (uses the repo's web/node_modules).
import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
export const REPO = join(here, '..', '..', '..', '..')
const { chromium } = await import(join(REPO, 'web', 'node_modules', 'playwright', 'index.mjs'))

export function evidence(runId) {
  const dir = join(REPO, 'data', 'verify', runId)
  mkdirSync(join(dir, 'shots'), { recursive: true })
  return { dir, base: `http://127.0.0.1:${readFileSync(join(dir, 'port'), 'utf8').trim()}` }
}

export async function open(runId) {
  const ev = evidence(runId)
  const browser = await chromium.launch()
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
  const errors = []
  page.on('pageerror', e => errors.push(String(e)))
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
  const shot = async (name) => { const p = join(ev.dir, 'shots', `${name}.png`); await page.screenshot({ path: p }); return p }
  const aria = async (name, locator = page.locator('body')) =>
    writeFileSync(join(ev.dir, 'shots', `${name}.aria.txt`), await locator.ariaSnapshot())
  const save = (name, data) => writeFileSync(join(ev.dir, name), typeof data === 'string' ? data : JSON.stringify(data, null, 2))
  return { browser, page, errors, shot, aria, save, ...ev }
}
