import { expect, test } from '@playwright/test'

const detailPath = '/algorithms/compliance_risk_scorer_onnx/1.0.0/onnx'
const activateEndpoint = 'http://127.0.0.1:18088/algorithms/compliance_risk_scorer_onnx/1.0.0/onnx/activate'
const conflictDetailPath = '/algorithms/decision_plan_recommender_onnx/1.0.0/onnx'
const conflictDeleteEndpoint = 'http://127.0.0.1:18088/algorithms/decision_plan_recommender_onnx/1.0.0/onnx'

test.afterEach(async ({ request }) => {
  // Restore shared test state even if a UI assertion fails midway.
  await request.post(activateEndpoint, { data: {} })
})

test('keeps a disabled algorithm disabled after validation and allows reactivation', async ({ page }) => {
  test.setTimeout(60_000)
  await page.goto(detailPath)

  await expect(page.getByRole('heading', { name: 'Compliance Risk Scorer ONNX' })).toBeVisible()
  await expect(page.getByText('已激活', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: /禁用/ }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认禁用' }).click()
  await expect(page.getByText('禁用成功，当前状态：disabled')).toBeVisible()
  await expect(page.getByText('已禁用', { exact: true })).toBeVisible()

  await page.getByRole('button', { name: /重新验证/ }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认重新验证' }).click()
  await expect(page.getByText('重新验证成功，当前状态：disabled')).toBeVisible()
  await expect(page.getByText('已禁用', { exact: true })).toBeVisible()
  const activateButton = page.getByRole('button', { name: /激\s*活/ }).first()
  await expect(activateButton).toBeVisible()

  await activateButton.click()
  await page.getByRole('dialog').getByRole('button', { name: /确认激活/ }).click()
  await expect(page.getByText('激活成功，当前状态：active')).toBeVisible()
  await expect(page.getByText('已激活', { exact: true })).toBeVisible()
})

test('shows the real 409 conflict when the page lifecycle state is stale', async ({ page, request }) => {
  await page.goto(conflictDetailPath)
  await expect(page.getByText('已激活', { exact: true })).toBeVisible()

  const deleteResponse = await request.delete(conflictDeleteEndpoint)
  expect(deleteResponse.ok()).toBe(true)

  // The page still renders the action from its previously loaded active state.
  await page.getByRole('button', { name: /禁用/ }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认禁用' }).click()

  await expect(page.getByText('Only validated or active algorithms can be disabled.')).toBeVisible()
})
