import { expect, test } from '@playwright/test'

test('play a match as a human from the browser', async ({ page, request }) => {
  await page.goto('/#/new')
  await page.getByTestId('format-table').click()
  await page.getByTestId('add-human').click()
  await page.getByTestId('add-mathematician').click()
  await page.getByTestId('pace-0').click()
  await expect(page.getByTestId('launch-btn')).toHaveText('Start and play')
  await page.getByTestId('launch-btn').click()

  await expect(page).toHaveURL(/#\/match\/match_.*\?seat=.+&as=You/)
  const bar = page.getByTestId('action-bar')
  // act on a few decisions: call or check whenever it's our turn
  for (let i = 0; i < 3; i++) {
    await expect(bar).toContainText('Your turn', { timeout: 15_000 })
    const check = page.getByTestId('act-check')
    if (await check.isVisible()) await check.click()
    else await page.getByTestId('act-call').click()
    await expect(bar).not.toContainText('Your turn')
  }
  // our own cards are face up; the opponent's are not revealed to us before showdown
  await expect(page.getByTestId('seat-You').getByTestId('card')).toHaveCount(2)

  // playing live: no timeline or transport, just the live bar
  await expect(page.getByTestId('play-bar')).toBeVisible()
  await expect(page.getByTestId('scrubber')).toHaveCount(0)

  // keyboard: C checks or calls on our turn, and the dock confirms it
  await expect(bar).toContainText('Your turn', { timeout: 15_000 })
  await page.keyboard.press('c')
  await expect(bar).toContainText(/You (checked|called)/)

  // review during our turn: controls appear, the banner shows our clock, and
  // Back to the table returns to the live table and the dock
  await expect(bar).toContainText('Your turn', { timeout: 15_000 })
  await page.getByTestId('review').click()
  await expect(page.getByTestId('review-banner')).toContainText("It's your turn")
  await expect(page.getByTestId('scrubber')).toBeVisible()
  await page.getByTestId('step-back').click()
  await page.getByTestId('back-to-table').click()
  await expect(page.getByTestId('play-bar')).toBeVisible()
  await expect(page.getByTestId('scrubber')).toHaveCount(0)
  await expect(bar).toContainText('Your turn')

  const key = decodeURIComponent(page.url().split('#/match/')[1].split('?')[0])
  const ev = await (await request.get(`/api/matches/${key}/events`)).json()
  expect(ev.events.filter((e: any) => e.type === 'decision' && e.bot === 'You').length).toBeGreaterThanOrEqual(3)
  await request.post(`/api/jobs/${key}/cancel`)
})

test('LLM seat shows how to connect', async ({ page, request }) => {
  await page.goto('/#/new')
  await page.getByTestId('add-mcp').click()
  await page.getByTestId('add-shark').click()
  await page.getByTestId('launch-btn').click()
  await expect(page.getByTestId('mcp-notice')).toContainText('claude mcp add')
  await expect(page.getByTestId('waiting')).toContainText('claude', { timeout: 15_000 })
  const key = decodeURIComponent(page.url().split('#/match/')[1])
  await request.post(`/api/jobs/${key}/cancel`)
})
