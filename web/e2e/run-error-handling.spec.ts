import { expect, test, type Page } from '@playwright/test'

const algorithm = {
  algorithm_id: 'mock_python_service',
  version: '1.0.0',
  display_name: 'Mock Python Service',
  backend_type: 'python_http_service',
  registry_status: 'active',
  card_status: 'validated',
  task_family: 'testing',
  capabilities: ['predict'],
  modalities: { input: ['structured_json'], output: ['structured_json'] },
  deployments: [],
  ready_endpoints: [],
  operational_functions: [],
  agent_card: {
    summary: 'Error handling fixture.',
    when_to_use: [],
    when_not_to_use: [],
    input_description: 'Test input',
    output_description: 'Test output',
    examples: [
      { input: { value: 1 }, output: { value: 1 } },
      { input: { value: 2 }, output: { value: 2 } },
    ],
  },
  constraints: {},
  performance: {},
  resource_requirements: {},
  model_profile: {},
  safety: {},
  input_schema_summary: {},
  output_schema_summary: {},
}

const inputSchema = {
  type: 'object',
  required: ['value'],
  properties: { value: { type: 'number' } },
  additionalProperties: false,
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/health', (route) => route.fulfill({
    json: {
      ok: true,
      status: 'ok',
      registry_path: 'test-registry.json',
      execution_log_path: 'test-execution.jsonl',
      operational_function_catalog_path: 'test-functions.yaml',
      runner_cache_size: 0,
    },
  }))
  await page.route('**/api/algorithms?active_only=false', (route) => route.fulfill({
    json: { ok: true, count: 1, filter: { active_only: false }, algorithms: [algorithm] },
  }))
  await page.route('**/api/algorithms/mock_python_service/1.0.0/python_http_service/schemas/input',
    (route) => route.fulfill({ json: inputSchema }))
})

async function invoke(page: Page) {
  await page.goto('/invoke')
  await expect(page.getByText(/Mock Python Service · 1\.0\.0 · Python HTTP/)).toBeVisible()
  await page.getByRole('button', { name: '执行调用' }).click()
}

test('shows a 503 service-unavailable error without degrading to UNKNOWN_ERROR', async ({ page }) => {
  const requestBodies: unknown[] = []
  await page.route('**/api/run', (route) => {
    const request = route.request().postDataJSON()
    requestBodies.push(request)
    if (requestBodies.length === 1) {
      return route.fulfill({
        status: 503,
        json: {
          ok: false,
          error_code: 'SERVICE_UNAVAILABLE',
          message: 'Python算法服务当前不可用，请检查服务健康状态。',
        },
      })
    }
    return route.fulfill({
      json: {
        ok: true,
        request_id: request.request_id,
        trace_id: request.trace_id,
        algorithm_id: request.algorithm_id,
        version: request.version,
        backend_type: request.backend_type,
        outputs: { recovered: true },
        usage: { latency_ms: 12 },
        error: null,
      },
    })
  })

  await invoke(page)

  await expect(page.getByText('调用失败：SERVICE_UNAVAILABLE')).toBeVisible()
  await expect(page.locator('.ant-alert-description').filter({
    hasText: 'Python算法服务当前不可用，请检查服务健康状态。',
  })).toBeVisible()
  await expect(page.getByText('调用失败：UNKNOWN_ERROR')).toHaveCount(0)

  await page.getByRole('button', { name: '重试本次请求' }).click()
  await expect(page.getByText('调用成功', { exact: true })).toBeVisible()
  await expect(page.getByText(/"recovered": true/)).toBeVisible()
  expect(requestBodies).toHaveLength(2)
  expect(requestBodies[1]).toEqual(requestBodies[0])
})

test('shows a 504 service-timeout error and preserves backend request context', async ({ page }) => {
  await page.route('**/api/run', (route) => route.fulfill({
    status: 504,
    json: {
      ok: false,
      request_id: 'req-timeout-e2e',
      trace_id: 'trace-timeout-e2e',
      algorithm_id: 'mock_python_service',
      version: '1.0.0',
      error_code: 'SERVICE_TIMEOUT',
      message: '算法调用超过3000毫秒，已终止等待。',
    },
  }))

  await invoke(page)

  await expect(page.getByText('调用失败：SERVICE_TIMEOUT')).toBeVisible()
  await expect(page.locator('.ant-alert-description').filter({
    hasText: '算法调用超过3000毫秒，已终止等待。',
  })).toBeVisible()
  await expect(page.getByText('req-timeout-e2e')).toBeVisible()
  await expect(page.getByText('trace-timeout-e2e')).toBeVisible()
  await expect(page.getByRole('button', { name: '重试本次请求' })).toBeVisible()
})

test('maps a disconnected request to NETWORK_ERROR and recovers on retry', async ({ page }) => {
  let attempts = 0
  await page.route('**/api/run', (route) => {
    attempts += 1
    if (attempts === 1) return route.abort('connectionrefused')
    const request = route.request().postDataJSON()
    return route.fulfill({
      json: {
        ok: true,
        request_id: request.request_id,
        trace_id: request.trace_id,
        algorithm_id: request.algorithm_id,
        version: request.version,
        backend_type: request.backend_type,
        outputs: { recovered_from_network_error: true },
        usage: {},
        error: null,
      },
    })
  })

  await invoke(page)
  await expect(page.getByText('调用失败：NETWORK_ERROR')).toBeVisible()

  await page.getByRole('button', { name: '重试本次请求' }).click()
  await expect(page.getByText('调用成功', { exact: true })).toBeVisible()
  await expect(page.getByText(/"recovered_from_network_error": true/)).toBeVisible()
  expect(attempts).toBe(2)
})

test('loads another algorithm-card request template before invocation', async ({ page }) => {
  let submittedValue: unknown
  await page.route('**/api/run', (route) => {
    const request = route.request().postDataJSON()
    submittedValue = request.inputs.value
    return route.fulfill({
      json: {
        ok: true,
        request_id: request.request_id,
        trace_id: request.trace_id,
        algorithm_id: request.algorithm_id,
        version: request.version,
        backend_type: request.backend_type,
        outputs: { value: request.inputs.value },
        usage: {},
        error: null,
      },
    })
  })

  await page.goto('/invoke')
  await expect(page.getByText(/Mock Python Service · 1\.0\.0 · Python HTTP/)).toBeVisible()
  const templateSelect = page.getByRole('combobox', { name: '请求模板' })
  await templateSelect.click()
  await templateSelect.press('ArrowDown')
  await templateSelect.press('Enter')
  await page.getByRole('button', { name: '加载模板' }).click()
  await page.getByRole('tab', { name: 'JSON 模式' }).click()
  await expect(page.locator('textarea.code-textarea').first()).toHaveValue(/"value": 2/)

  await page.getByRole('button', { name: '执行调用' }).click()
  await expect(page.getByText('调用成功', { exact: true })).toBeVisible()
  expect(submittedValue).toBe(2)
})
