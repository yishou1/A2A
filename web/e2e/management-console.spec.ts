import { expect, test } from '@playwright/test'

const healthResponse = {
  ok: true,
  status: 'ok',
  registry_path: 'D:\\algolib-test\\registry.json',
  execution_log_path: 'D:\\algolib-test\\execution.jsonl',
  operational_function_catalog_path: 'config/operational_function_catalog.yaml',
  runner_cache_size: 1,
}

const algorithm = {
  algorithm_id: 'compliance_risk_scorer_onnx',
  version: '1.0.0',
  display_name: '合规风险评分器',
  backend_type: 'onnx',
  registry_status: 'active',
  card_status: 'validated',
  task_family: 'risk_scoring',
  capabilities: ['risk_scoring', 'policy_validation'],
  modalities: { input: ['tabular'], output: ['score'] },
  deployments: [
    {
      deploy_id: 'local-onnx',
      node_id: 'local',
      zone: 'development',
      endpoint: '',
      deploy_status: 'ready',
      status_message: 'ready',
    },
  ],
  ready_endpoints: [],
  operational_functions: [],
  agent_card: {
    summary: '对任务方案进行合规风险评分。',
    when_to_use: [],
    when_not_to_use: [],
    input_description: '任务方案特征',
    output_description: '风险评分',
    examples: [],
  },
  constraints: {},
  performance: {},
  resource_requirements: {},
  model_profile: {},
  safety: {},
  input_schema_summary: {},
  output_schema_summary: {},
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/health', async (route) => {
    await route.fulfill({ json: healthResponse })
  })
  await page.route('**/api/algorithms?active_only=false', async (route) => {
    await route.fulfill({
      json: {
        ok: true,
        count: 1,
        filter: { active_only: false },
        algorithms: [algorithm],
      },
    })
  })
})

test('loads the overview and filters the algorithm catalog', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: '运行总览' })).toBeVisible()
  await expect(page.getByText('服务正常')).toBeVisible()
  await expect(page.getByText('D:\\algolib-test\\registry.json')).toBeVisible()

  await page.getByText('算法目录', { exact: true }).click()
  await expect(page).toHaveURL(/\/algorithms$/)
  await expect(page.getByRole('heading', { name: '算法目录' })).toBeVisible()
  await expect(page.getByText('合规风险评分器')).toBeVisible()
  await expect(page.getByText('1 / 1 就绪')).toBeVisible()

  const search = page.getByPlaceholder('搜索算法名称或 ID')
  await search.fill('不存在的算法')
  await expect(page.getByText('没有符合当前筛选条件的算法')).toBeVisible()
  await expect(page.getByText('共 0 项')).toBeVisible()

  await search.fill('compliance')
  await expect(page.getByText('合规风险评分器')).toBeVisible()
  await expect(page.getByText('共 1 项')).toBeVisible()
})
