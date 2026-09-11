import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e-real',
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: 'http://127.0.0.1:5174',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium-real-onnx',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: [
    {
      command: 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ../scripts/start_frontend_python_e2e_service.ps1',
      url: 'http://127.0.0.1:9011/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File ../scripts/start_frontend_real_e2e_backend.ps1',
      url: 'http://127.0.0.1:18088/health',
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5174',
      url: 'http://127.0.0.1:5174',
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        ...process.env,
        ALGOLIB_API_TARGET: 'http://127.0.0.1:18088',
      },
    },
  ],
})
