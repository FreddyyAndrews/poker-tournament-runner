#!/usr/bin/env node
// Open a match in the UI, step to <bot>'s first decision, capture the table and the
// decision inspector.   node watch.mjs <run_id> <match_key> <bot>
import { open } from './_browser.mjs'

const [runId, key, bot] = process.argv.slice(2)
if (!runId || !key || !bot) { console.error('usage: watch.mjs <run_id> <match_key> <bot>'); process.exit(2) }
const s = await open(runId)
const { page } = s
try {
  await page.goto(`${s.base}/#/match/${key}`)
  await page.getByTestId('poker-table').waitFor()
  await page.getByTestId(`seat-${bot}`).waitFor()
  await s.shot('watch_1_table')

  // Step forward until the newest hand-log row is a decision by <bot>.
  let found = false
  for (let i = 0; i < 400 && !found; i++) {
    await page.keyboard.press('ArrowRight')
    const rows = page.locator('[data-testid=log-row][data-type=decision]')
    const n = await rows.count()
    if (n && (await rows.nth(n - 1).locator('.who').textContent()) === bot) found = true
  }
  if (!found) throw new Error(`no decision by ${bot} found in the first 400 events`)
  await s.shot('watch_2_bot_acting')
  await page.getByTestId('tab-decision').click()
  await page.getByTestId('inspector').getByText('What it saw').waitFor()
  await s.shot('watch_3_inspector')
  await s.aria('watch_3_inspector', page.getByTestId('inspector'))
  const position = await page.getByTestId('position').textContent()
  const inspector = await page.getByTestId('inspector').innerText()
  await page.getByTestId('tab-chart').click()
  await s.shot('watch_4_stacks')
  s.save('watch.json', { key, bot, position, inspector_excerpt: inspector.slice(0, 1500), page_errors: s.errors })
  console.log(JSON.stringify({ ok: true, position, page_errors: s.errors.length }))
} catch (e) {
  await s.shot('watch_failed')
  console.error(String(e)); process.exitCode = 1
} finally { await s.browser.close() }
