import { defineConfig, devices } from '@playwright/test';

// The UI (images/incident-api/ui) is built and served by `vite preview`; every
// test mocks the JSON API with page.route and the incident WebSocket with
// page.routeWebSocket, so no backend is needed.
const PORT = 4173;
const UI = '../../images/incident-api/ui';

export default defineConfig({
  testDir: '.',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: process.env.CI ? 2 : 1,
  workers: process.env.CI ? 1 : undefined,
  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: process.env.BASE_URL ?? `http://localhost:${PORT}`,
    trace: 'on-first-retry',
  },

  webServer: process.env.BASE_URL
    ? undefined
    : {
        command: `npm --prefix ${UI} run build && npm --prefix ${UI} run preview -- --port ${PORT} --strictPort`,
        url: `http://localhost:${PORT}/`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
