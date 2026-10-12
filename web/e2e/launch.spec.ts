import { expect, test } from '@playwright/test'

test('launch a heads-up match from the form and land on the live table', async ({ page, request }) => {
  await page.goto('/#/new')
  await page.getByTestId('format-table').click()
  await page.getByTestId('add-shark').click()
  await page.getByTestId('add-aggressor').click()
  await expect(page.getByTestId('summary')).toContainText('Heads-up match')
  await page.getByTestId('pace-250').click()
  await page.getByTestId('launch-btn').click()

  await expect(page).toHaveURL(/#\/match\/match_/)
  await expect(page.getByTestId('status')).toHaveText('Live')
  await expect(page.getByTestId('seat-shark')).toBeVisible()
  const key = decodeURIComponent(page.url().split('#/match/')[1])
  await request.post(`/api/jobs/${key}/cancel`)
})

test('launch a bracket and watch its live match from the run page', async ({ page, request }) => {
  await page.goto('/#/new')
  await page.getByTestId('format-bracket').click()
  for (const b of ['shark', 'aggressor', 'mathematician', 'ref_bot_2']) await page.getByTestId(`add-${b}`).click()
  await page.getByTestId('blinds-turbo').click()
  await expect(page.getByTestId('summary')).toContainText('4-bot knockout bracket')
  await page.getByTestId('launch-btn').click()

  await expect(page).toHaveURL(/#\/run\/bracket_/)
  await expect(page.getByTestId('now-playing')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('watch').first().click()
  await expect(page).toHaveURL(/#\/match\/bracket_.*\/matches\/r1_m/)
  await expect(page.getByTestId('status')).toHaveText('Live')
  const runId = decodeURIComponent(page.url().split('#/match/')[1]).split('/')[0]
  await request.post(`/api/jobs/${runId}/cancel`)
})

test('run a quick series and see the results', async ({ page }) => {
  await page.goto('/#/new')
  await page.getByTestId('format-table').click()
  await page.getByTestId('add-shark').click()
  await page.getByTestId('add-mathematician').click()
  await page.getByTestId('mode-fixed').click()
  await page.getByTestId('hands').fill('10')
  await page.getByTestId('repeat').selectOption('series')
  await page.getByTestId('runs').fill('3')
  await expect(page.getByTestId('summary')).toContainText('played 3 times')
  await page.getByTestId('launch-btn').click()

  await expect(page).toHaveURL(/#\/run\/series_table_/)
  await expect(page.getByText('Results across runs')).toBeVisible({ timeout: 20_000 })
})

test('the form explains what is missing', async ({ page }) => {
  await page.goto('/#/new')
  await expect(page.getByTestId('launch-btn')).toBeDisabled()
  await page.getByTestId('add-shark').click()
  await expect(page.getByTestId('launch-btn')).toBeDisabled()
  await page.getByTestId('add-shark').click()
  await expect(page.getByTestId('launch-btn')).toBeEnabled()
})
