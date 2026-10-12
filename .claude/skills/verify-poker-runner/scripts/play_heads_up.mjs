#!/usr/bin/env node
// Play heads-up against <bot> as a human, through the real UI: New page -> You + bot ->
// Start and play -> act from the action bar until the match ends.
//   node play_heads_up.mjs <run_id> <bot> [hands=5]
// First decision raises (if allowed) to exercise the raise path; then check/call.
import { open } from './_browser.mjs'

const [runId, bot, handsArg = '5'] = process.argv.slice(2)
if (!runId || !bot) { console.error('usage: play_heads_up.mjs <run_id> <bot> [hands]'); process.exit(2) }
const s = await open(runId)
const { page } = s
const actions = []
try {
  await page.goto(`${s.base}/#/new`)
  await page.getByTestId('format-table').click()
  await page.getByTestId('add-human').click()
  await page.getByTestId(`add-${bot}`).click()
  await page.getByTestId('mode-cash').click()
  await page.getByTestId('hands').fill(handsArg)
  await page.getByTestId('pace-250').click()
  await s.shot('play_1_form')
  await page.getByTestId('launch-btn').click()
  await page.waitForURL(/#\/match\/.*\?seat=/)
  const key = decodeURIComponent(page.url().split('#/match/')[1].split('?')[0])

  const bar = page.getByTestId('action-bar')
  const deadline = Date.now() + 180_000
  while (Date.now() < deadline) {
    const text = await bar.innerText().catch(() => '')
    if (/match is over/i.test(text)) break
    if (!/Your turn/.test(text)) { await page.waitForTimeout(150); continue }
    if (actions.length === 0) { await s.shot('play_2_your_turn'); await s.aria('play_2_action_bar', bar) }
    const raise = page.getByTestId('act-raise'), check = page.getByTestId('act-check')
    let action
    if (actions.length === 0 && await raise.isVisible()) { await raise.click(); action = 'raise' }
    else if (await check.isVisible()) { await check.click(); action = 'check' }
    else { await page.getByTestId('act-call').click(); action = 'call' }
    actions.push(action)
    await page.waitForFunction(() => !document.querySelector('[data-testid=action-bar]')?.textContent?.includes('Your turn'),
                               null, { timeout: 10_000 }).catch(() => {})
  }
  await page.waitForTimeout(800)
  await s.shot('play_3_after')
  const ended = /match is over/i.test(await bar.innerText().catch(() => ''))
  s.save('play.json', { key, bot, actions, ended, page_errors: s.errors })
  console.log(JSON.stringify({ ok: ended && actions.length > 0, key, decisions: actions.length, ended }))
  if (!ended || !actions.length) process.exitCode = 1
} catch (e) {
  await s.shot('play_failed')
  console.error(String(e)); process.exitCode = 1
} finally { await s.browser.close() }
