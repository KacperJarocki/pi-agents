import { test, expect, Page } from '@playwright/test';
import * as path from 'path';

// The device overview (images/incident-api/app/static/devices.html, served at
// /) with the JSON API mocked through page.route (no backend needed).
const PAGE = path.resolve(__dirname, '../../images/incident-api/app/static/devices.html');
const NOW = Date.now();

function device(extra: Record<string, unknown> = {}) {
  return {
    mac: '02:00:00:00:00:0a',
    device_key: 'gw:02:00:00:00:00:0a',
    gateway_id: 'gw',
    hostname: 'zero',
    ip: '192.168.50.134',
    lease_expires_at_ms: NOW + 3_600_000,
    in_lease_table: true,
    last_seen_ms: NOW - 5_000,
    features: {
      '60': { flows: 31, distinct_targets: 25, failed_targets: 24, bytes: 4096, window_end_ms: NOW - 5_000 },
      '900': { flows: 310, distinct_targets: 40, failed_targets: 30, bytes: 2_500_000, window_end_ms: NOW - 5_000 },
    },
    incidents: { active: 1, total: 3, last_detected_ms: NOW - 10_000, severity: 'MEDIUM' },
    ...extra,
  };
}

function incident(id: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    incident_id: id,
    revision: 3,
    status,
    severity: 'LOW',
    device_key: 'gw:02:00:00:00:00:0a',
    horizons: [
      { horizon_seconds: 60, value: 25, open_threshold: 20, over_threshold: true },
      { horizon_seconds: 900, value: 30, open_threshold: 40, over_threshold: false },
    ],
    episode_start_ms: NOW - 600_000,
    detected_at_ms: NOW - 60_000,
    ...extra,
  };
}

async function open(page: Page, devices: unknown[], leases = { available: true, error: null as string | null }) {
  await page.route('**/api/v2/devices', (route) => route.fulfill({ json: { devices, leases, generated_at_ms: NOW } }));
  await page.route('**/api/v2/incidents?**', (route) =>
    route.fulfill({ json: { cursor: 3, incidents: [incident('i-open', 'UPDATE'), incident('i-old', 'RESOLVE', { severity: 'HIGH' })] } }),
  );
  await page.route('**/api/v2/incidents/i-old/transitions', (route) =>
    route.fulfill({
      json: {
        incident_id: 'i-old',
        transitions: [incident('i-old', 'OPEN', { revision: 1 }), incident('i-old', 'UPDATE', { revision: 2, severity: 'HIGH' }), incident('i-old', 'RESOLVE')],
      },
    }),
  );
  await page.route('**/devices-page/**', (route) => route.fulfill({ path: PAGE, contentType: 'text/html' }));
  await page.goto('/devices-page/');
}

test('lists devices with hostname, IP, state and window stats', async ({ page }) => {
  await open(page, [
    device(),
    device({ mac: '02:00:00:00:00:0b', device_key: 'gw:02:00:00:00:00:0b', hostname: 'MacBookAir', ip: '192.168.50.101', incidents: { active: 0, total: 0, last_detected_ms: null, severity: null } }),
    device({ mac: '02:00:00:00:00:0c', device_key: 'gw:02:00:00:00:00:0c', hostname: 'printer', last_seen_ms: NOW - 3_600_000, incidents: { active: 0, total: 1, last_detected_ms: null, severity: null } }),
    device({ mac: '02:00:00:00:00:0d', device_key: null, hostname: 'iPhone', last_seen_ms: null, features: {}, incidents: { active: 0, total: 0, last_detected_ms: null, severity: null } }),
  ]);
  const rows = page.getByTestId('devices').locator('tr');
  await expect(rows).toHaveCount(4);
  const zero = page.locator('tr[data-device="gw:02:00:00:00:00:0a"]');
  await expect(zero.getByTestId('name')).toHaveText('zero');
  await expect(zero.getByTestId('ip')).toHaveText('192.168.50.134');
  await expect(zero.getByTestId('state')).toHaveText('Alert · MEDIUM');
  await expect(zero.getByTestId('incident-count')).toHaveText('1 / 3');
  await expect(page.locator('tr[data-device="gw:02:00:00:00:00:0b"]').getByTestId('state')).toHaveText('Clean');
  await expect(page.locator('tr[data-device="gw:02:00:00:00:00:0c"]').getByTestId('state')).toHaveText('Idle');
  await expect(page.locator('tr[data-device="02:00:00:00:00:0d"]').getByTestId('state')).toHaveText('No traffic');
  await expect(page.getByTestId('lease-note')).toBeHidden();
  await expect(page.getByTestId('history')).toBeHidden();
});

test('clicking a device shows its incident history and an incident its transitions', async ({ page }) => {
  await open(page, [device()]);
  await page.locator('tr[data-device="gw:02:00:00:00:00:0a"]').click();
  await expect(page.getByTestId('history')).toBeVisible();
  await expect(page.locator('#history-title')).toHaveText('Incident history · zero');
  const history = page.getByTestId('history-rows').locator('tr[data-incident]');
  await expect(history).toHaveCount(2);
  await expect(history.nth(1)).toContainText('Resolved');
  await expect(page).toHaveURL(/device=gw%3A02%3A00%3A00%3A00%3A00%3A0a/);

  await history.nth(1).click();
  const timeline = page.getByTestId('timeline');
  await expect(timeline.locator('tbody tr')).toHaveCount(3);
  await expect(timeline).toContainText('OPEN');
  await expect(timeline).toContainText('RESOLVE');
});

test('without leases devices are listed by MAC with a note', async ({ page }) => {
  await open(page, [device({ hostname: null, ip: null })], { available: false, error: 'gateway-agent unavailable: ConnectError' });
  await expect(page.getByTestId('lease-note')).toContainText('gateway-agent unavailable');
  await expect(page.locator('tr[data-device="gw:02:00:00:00:00:0a"]').getByTestId('name')).toHaveText('unknown');
});
