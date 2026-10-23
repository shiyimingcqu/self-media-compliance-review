import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  use: { baseURL: 'http://127.0.0.1:5175', channel: process.env.PLAYWRIGHT_CHANNEL || undefined, trace: 'retain-on-failure', viewport: { width: 1440, height: 1080 } },
  webServer: [
    { command: 'python scripts/e2e-backend.py', url: 'http://127.0.0.1:8001/health', reuseExistingServer: false, timeout: 30_000 },
    { command: 'npm run dev --workspace frontend -- --port 5175', url: 'http://127.0.0.1:5175', env: { API_PROXY_TARGET: 'http://127.0.0.1:8001' }, reuseExistingServer: false, timeout: 30_000 },
  ],
});
