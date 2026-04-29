import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: process.env.CI ? 2 : 1,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: 'http://localhost:3000',
    trace: 'on-first-retry',
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: ['integration/**/*.spec.ts'],
    },
    {
      name: 'integration',
      use: { ...devices['Desktop Chrome'] },
      testMatch: ['integration/**/*.spec.ts'],
    },
  ],

  // Requires `podman-compose up --build -d` to already be running,
  // OR set reuseExistingServer: false to auto-start (slow, ~2 min build).
  webServer: {
    command: 'podman-compose -f ../../docker-compose.yml up --build -d && sleep 5 && podman-compose -f ../../docker-compose.yml logs -f --no-color',
    url: 'http://localhost:3000',
    timeout: 180_000,
    reuseExistingServer: true,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
