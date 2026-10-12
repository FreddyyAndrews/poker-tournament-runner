import { expect, test, type APIRequestContext } from '@playwright/test'

async function startMatch(request: APIRequestContext, body: Record<string, unknown>) {
  const r = await request.post('/api/matches', { data: body })
  expect(r.status()).toBe(201)
  return (await r.json()).match_key as string
}

async function waitDone(request: APIRequestContext, id: string) {
  await expect.poll(async () => (await (await request.get(`/api/jobs/${id}`)).json()).status,
                    { timeout: 30_000 }).toBe('complete')
}

test('replay a finished match: table, controls, log, inspector, chart', async ({ page, request }) => {
  const key = await startMatch(request, {
    bots: ['shark', 'aggressor', 'mathematician'], hands: 15, seed: 3, pace_ms: 0, id: 'e2e_replay' })
  await waitDone(request, key)

  await page.goto(`/#/match/${key}`)
  await expect(page.getByTestId('status')).toHaveText('Complete')
  await expect(page.getByTestId('poker-table')).toBeVisible()
  for (const bot of ['shark', 'aggressor', 'mathematician']) await expect(page.getByTestId(`seat-${bot}`)).toBeVisible()
  await expect(page.getByTestId('position')).toContainText('Hand 1 of 15')

  // stepping advances the cursor and fills the hand log
  const before = await page.getByTestId('position').textContent()
  for (let i = 0; i < 6; i++) await page.getByTestId('step').click()
  await expect(page.getByTestId('position')).not.toHaveText(before!)
  await expect(page.getByTestId('log-row').first()).toBeVisible()

  // next hand via keyboard
  await page.keyboard.press('Shift+ArrowRight')
  await expect(page.getByTestId('position')).toContainText('Hand 2 of 15')

  // clicking a decision opens the inspector
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  await page.getByTestId('log-row').filter({ hasNot: page.locator('.sys') }).last().click()
  await page.getByTestId('tab-decision').click()
  await expect(page.getByTestId('inspector')).toContainText('What it saw')

  // playing at 32x moves through hands
  await page.getByTestId('speed-32').click()
  await page.getByTestId('play').click()
  await expect(page.getByTestId('position')).toContainText(/Hand (3|4|5|6|7|8|9|1\d) of 15/, { timeout: 15_000 })
  await page.getByTestId('play').click()

  await page.getByTestId('tab-chart').click()
  await expect(page.getByTestId('stack-chart')).toBeVisible()
})

test('follow a live, paced match', async ({ page, request }) => {
  const key = await startMatch(request, {
    bots: ['shark', 'aggressor'], mode: 'freezeout', blinds: 'turbo', seed: 9, pace_ms: 120, id: 'e2e_live' })
  await page.goto(`/#/match/${key}`)
  await expect(page.getByTestId('status')).toHaveText('Live')
  await expect(page.getByTestId('live')).toHaveText('LIVE')

  // events keep arriving while we watch
  const count = async () => Number((await page.getByTestId('position').textContent())!.match(/event \d+ \/ (\d+)/)![1])
  const first = await count()
  await expect.poll(count, { timeout: 15_000 }).toBeGreaterThan(first + 5)

  // scrub back: leaves live; Go live returns to the newest event
  await page.getByTestId('step-back').click()
  await page.getByTestId('step-back').click()
  await expect(page.getByTestId('live')).toHaveText('GO LIVE')
  await page.getByTestId('live').click()
  await expect(page.getByTestId('live')).toHaveText('LIVE')

  await request.post(`/api/jobs/${key}/cancel`)
})

test('recent list links to matches', async ({ page, request }) => {
  const key = await startMatch(request, { bots: ['shark', 'ref_bot_2'], hands: 3, seed: 1, pace_ms: 0, id: 'e2e_list' })
  await waitDone(request, key)
  await page.goto('/#/')
  await page.getByTestId('run-row').filter({ hasText: 'e2e_list' }).click()
  await expect(page.getByTestId('match-title')).toHaveText('e2e_list')
})
