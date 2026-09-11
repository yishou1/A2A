import assert from 'node:assert/strict'
import { chromium } from '@playwright/test'

const amosUrl = (process.env.AMOS_INTEGRATED_URL || 'http://127.0.0.1:5000').replace(/\/+$/, '')
const browser = await chromium.launch({ headless: true })

try {
  const context = await browser.newContext()
  const amosPage = await context.newPage()
  await amosPage.goto(`${amosUrl}/`)
  await amosPage.getByRole('tab', { name: '算法与功能' }).click()

  const entry = amosPage.locator('#open-algorithm-console')
  await entry.waitFor()
  assert.equal(await entry.getAttribute('href'), '/algolib/algorithms')
  assert.equal(await entry.getAttribute('target'), '_blank')

  const popupPromise = context.waitForEvent('page')
  await entry.click()
  const consolePage = await popupPromise
  await consolePage.getByRole('heading', { name: '算法目录' }).waitFor()

  assert.equal(consolePage.url(), `${amosUrl}/algolib/algorithms`)
  assert.equal(await consolePage.getByText('服务正常').isVisible(), true)
  const algorithmRows = await consolePage.locator('tbody tr').count()
  assert.ok(algorithmRows > 0, 'The integrated algorithm catalog is empty')
  assert.equal(
    await consolePage.getByRole('link', { name: 'AMOS 任务台' }).getAttribute('href'),
    '/',
  )

  console.log(
    JSON.stringify({
      amosUrl,
      consoleUrl: consolePage.url(),
      serviceReady: true,
      algorithmRows,
    }),
  )
} finally {
  await browser.close()
}
