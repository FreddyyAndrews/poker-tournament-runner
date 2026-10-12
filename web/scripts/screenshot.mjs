// Screenshot helper for checking the UI (used by agents too):
//   node scripts/screenshot.mjs <url> <out.png> [waitMs] [actions...]
import { chromium } from 'playwright'
const [url, out, wait = '1500', ...actions] = process.argv.slice(2)
const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 860 }, deviceScaleFactor: 1 })
const errors = []
page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
page.on('pageerror', e => errors.push(String(e)))
await page.goto(url)
await page.waitForTimeout(Number(wait))
for (const a of actions) {
  const i = a.indexOf('='); const kind = a.slice(0, i), arg = a.slice(i + 1)
  if (kind === 'click') await page.click(arg)
  if (kind === 'key') await page.keyboard.press(arg)
  if (kind === 'wait') await page.waitForTimeout(Number(arg))
}
await page.screenshot({ path: out })
console.log(JSON.stringify({ errors }))
await browser.close()
