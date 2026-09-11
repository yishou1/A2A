import { expect, test } from '@playwright/test'

test('invokes a real ONNX algorithm and opens its function trace', async ({ page }) => {
  test.setTimeout(60_000)
  const traceId = `trace-e2e-onnx-${Date.now()}`

  await page.goto('/invoke?algorithm_id=compliance_risk_scorer_onnx&version=1.0.0&backend_type=onnx')

  await expect(page.getByRole('heading', { name: '在线调用' })).toBeVisible()
  await expect(page.getByText(/Compliance Risk Scorer ONNX · 1\.0\.0 · ONNX/)).toBeVisible()

  await page.getByRole('tab', { name: 'JSON 模式' }).click()
  await page.locator('textarea.code-textarea').first().fill(JSON.stringify({
    features: [[0, 1, 0.2, 0, 2, 0]],
  }, null, 2))

  await page.getByRole('textbox', { name: /Trace ID/ }).fill(traceId)

  const functionSelect = page.getByRole('combobox', { name: /业务功能点/ })
  await functionSelect.click()
  await functionSelect.press('ArrowDown')
  await functionSelect.press('Enter')

  await page.getByRole('button', { name: '执行调用' }).click()

  await expect(page.getByText('调用成功', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('risk_probability')).toBeVisible()
  await expect(page.getByText('compliance_risk_scorer_onnx / 1.0.0')).toBeVisible()

  await page.getByRole('button', { name: '查看链路' }).click()
  await expect(page).toHaveURL(new RegExp(`/traces\\?trace_id=${traceId}$`))
  await expect(page.getByRole('heading', { name: '执行链路' })).toBeVisible()
  await expect(page.getByText(`Trace ${traceId}`)).toBeVisible()
  await expect(page.getByText('KC-21', { exact: true })).toBeVisible()
  await expect(page.getByText('compliance_risk_scorer_onnx / 1.0.0')).toBeVisible()
})

test('rejects invalid ONNX inputs before sending a run request', async ({ page }) => {
  let runRequestCount = 0
  page.on('request', (request) => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/run') {
      runRequestCount += 1
    }
  })

  await page.goto('/invoke?algorithm_id=compliance_risk_scorer_onnx&version=1.0.0&backend_type=onnx')
  await expect(page.getByText(/Compliance Risk Scorer ONNX · 1\.0\.0 · ONNX/)).toBeVisible()

  await page.getByRole('tab', { name: 'JSON 模式' }).click()
  await page.locator('textarea.code-textarea').first().fill(JSON.stringify({
    features: [0, 1, 0.2, 0, 2, 0],
  }, null, 2))
  await page.getByRole('button', { name: '执行调用' }).click()

  await expect(page.getByText('请求编辑错误')).toBeVisible()
  await expect(page.getByText(/输入不符合 Schema/)).toBeVisible()
  expect(runRequestCount).toBe(0)
})
