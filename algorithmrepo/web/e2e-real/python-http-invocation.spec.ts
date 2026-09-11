import { expect, test } from '@playwright/test'

const trajectoryInputs = {
  track: {
    track_id: 'T-E2E-001',
    history: [
      { t: 0, x: 10, y: 18 },
      { t: 0.1, x: 10.4, y: 18.6 },
      { t: 0.2, x: 10.9, y: 19.1 },
      { t: 0.3, x: 11.3, y: 19.7 },
      { t: 0.4, x: 11.8, y: 20.2 },
    ],
    weapon_prep_sec: 2,
    flight_time_sec: 4,
  },
}

test('invokes a real Python HTTP algorithm and opens its function trace', async ({ page }) => {
  test.setTimeout(60_000)
  const traceId = `trace-e2e-python-${Date.now()}`

  await page.goto('/invoke?algorithm_id=trajectory_linear_predictor&version=1.0.0&backend_type=python_http_service')

  await expect(page.getByRole('heading', { name: '在线调用' })).toBeVisible()
  await expect(page.getByText(/Trajectory Linear Predictor · 1\.0\.0 · Python HTTP/)).toBeVisible()

  await page.getByRole('tab', { name: 'JSON 模式' }).click()
  await page.locator('textarea.code-textarea').first().fill(JSON.stringify(trajectoryInputs, null, 2))
  await page.getByRole('textbox', { name: /Trace ID/ }).fill(traceId)

  const functionSelect = page.getByRole('combobox', { name: /业务功能点/ })
  await functionSelect.click()
  await functionSelect.press('Enter')

  await page.getByRole('button', { name: '执行调用' }).click()

  await expect(page.getByText('调用成功', { exact: true })).toBeVisible({ timeout: 30_000 })
  const outputs = page.locator('.invocation-result-json').first()
  await expect(outputs).toContainText('velocity')
  await expect(outputs).toContainText('aim_point')
  await expect(outputs).toContainText('T-E2E-001')
  await expect(page.getByText('trajectory_linear_predictor / 1.0.0')).toBeVisible()

  await page.getByRole('button', { name: '查看链路' }).click()
  await expect(page).toHaveURL(new RegExp(`/traces\\?trace_id=${traceId}$`))
  await expect(page.getByRole('heading', { name: '执行链路' })).toBeVisible()
  await expect(page.getByText(`Trace ${traceId}`)).toBeVisible()
  await expect(page.getByText('KC-19', { exact: true })).toBeVisible()
  await expect(page.getByText('trajectory_linear_predictor / 1.0.0')).toBeVisible()
})
