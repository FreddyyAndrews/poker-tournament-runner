import { expect, test, type APIRequestContext } from '@playwright/test'

async function run(request: APIRequestContext, body: Record<string, unknown>) {
  const r = await request.post('/api/runs', { data: { pace_ms: 0, seed: 4, ...body } })
  expect(r.status()).toBe(201)
  const id = (await r.json()).run_id as string
  await expect.poll(async () => (await (await request.get(`/api/jobs/${id}`)).json()).status,
                    { timeout: 60_000 }).toBe('complete')
  return id
}

test('bracket dashboard shows the tree and verifies', async ({ page, request }) => {
  const id = await run(request, { format: 'bracket', bots: ['shark', 'aggressor', 'mathematician', 'ref_bot_2', 'template'],
                                  blinds: 'turbo', id: 'e2e_br' })
  await page.goto(`/#/run/${id}`)
  await expect(page.getByTestId('bracket')).toBeVisible()
  await expect(page.getByText('Quarter-finals')).toBeVisible()
  await expect(page.getByTestId('tie')).toHaveCount(7)
  await expect(page.getByTestId('standings-table')).toBeVisible()
  await page.getByTestId('verify').click()
  await expect(page.getByTestId('verify-result')).toContainText('Verified')
  await page.locator('[data-testid=tie][data-status=complete]').first().click()
  await expect(page).toHaveURL(/#\/match\/e2e_br\/matches\/r/)
})

test('round robin grid links to matches', async ({ page, request }) => {
  const id = await run(request, { format: 'round_robin', bots: ['shark', 'aggressor', 'mathematician'], hands: 20, id: 'e2e_rr' })
  await page.goto(`/#/run/${id}`)
  const grid = page.getByTestId('rr-grid')
  await expect(grid).toBeVisible()
  await expect(grid.locator('td.link')).toHaveCount(6)
  await grid.locator('td.link').first().click()
  await expect(page).toHaveURL(/#\/match\/e2e_rr\/matches\/.*_leg1/)
})

test('multi-table overview and watching one table', async ({ page, request }) => {
  const bots = Array(3).fill(['shark', 'aggressor', 'mathematician', 'ref_bot_2', 'template']).flat()
  const id = await run(request, { format: 'mtt', bots, table_size: 6, blinds: 'turbo', id: 'e2e_mtt' })
  await page.goto(`/#/run/${id}`)
  await expect(page.getByTestId('mtt-tables')).toBeVisible()
  await expect(page.getByText('Eliminations')).toBeVisible()
  await page.getByTestId('watch-table').first().click()
  await expect(page.getByTestId('match-title')).toHaveText(/^Table \d$/)
  await expect(page.getByTestId('poker-table')).toBeVisible()
})

test('series chart', async ({ page, request }) => {
  const id = await run(request, { format: 'bracket', bots: ['shark', 'aggressor', 'mathematician', 'ref_bot_2'],
                                  blinds: 'turbo', runs: 6, workers: 6, id: 'e2e_series' })
  await page.goto(`/#/run/${id}`)
  await expect(page.getByTestId('series-chart')).toBeVisible()
  await expect(page.getByTestId('series-table')).toContainText('aggressor')
})
