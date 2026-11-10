import { test, expect, Page } from '@playwright/test';
import { mockDevices, mockFeed } from './mocks';

// The device overview at `/` of images/incident-api/ui with the JSON API
// mocked through page.route and a silent incident WebSocket.
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

const quiet = { active: 0, total: 0, last_detected_ms: null, severity: null };

function incident(id: string, status: string, extra: Record<string, unknown> = {}) {
  return {
    incident_id: id,
    revision: 3,
    status,
    severity: 'LOW',
    rule_id: 'port-scan',
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

async function open(
  page: Page,
  devices: unknown[],
  leases = { available: true, error: null as string | null },
  actions: { block?: boolean } = {},
) {
  await mockFeed(page);
  await mockDevices(page, devices, leases, actions);
  await page.route('**/api/v2/incidents?**', (route) =>
    route.fulfill({
      json: { cursor: 3, incidents: [incident('i-open', 'UPDATE'), incident('i-old', 'RESOLVE', { severity: 'HIGH' })] },
    }),
  );
  await page.route('**/api/v2/incidents/i-old/transitions', (route) =>
    route.fulfill({
      json: {
        incident_id: 'i-old',
        transitions: [
          incident('i-old', 'OPEN', { revision: 1 }),
          incident('i-old', 'UPDATE', { revision: 2, severity: 'HIGH' }),
          incident('i-old', 'RESOLVE'),
        ],
      },
    }),
  );
  await page.goto('/');
}

const fleet = () => [
  device(),
  device({
    mac: '02:00:00:00:00:0b',
    device_key: 'gw:02:00:00:00:00:0b',
    hostname: 'MacBookAir',
    ip: '192.168.50.101',
    incidents: quiet,
  }),
  device({
    mac: '02:00:00:00:00:0c',
    device_key: 'gw:02:00:00:00:00:0c',
    hostname: 'printer',
    last_seen_ms: NOW - 3_600_000,
    incidents: { ...quiet, total: 1 },
  }),
  device({
    mac: '02:00:00:00:00:0d',
    device_key: null,
    hostname: 'iPhone',
    last_seen_ms: null,
    features: {},
    incidents: quiet,
  }),
];

test('lists devices with hostname, IP, state and window stats', async ({ page }) => {
  await open(page, fleet());
  const rows = page.getByTestId('devices').locator('tr');
  await expect(rows).toHaveCount(4);
  const zero = page.locator('tr[data-device="gw:02:00:00:00:00:0a"]');
  await expect(zero.getByTestId('name')).toHaveText('zero');
  await expect(zero.getByTestId('ip')).toHaveText('192.168.50.134');
  await expect(zero.getByTestId('state')).toHaveText('Alert · Medium');
  await expect(zero.getByTestId('incident-count')).toHaveText('1 / 3');
  await expect(page.locator('tr[data-device="gw:02:00:00:00:00:0b"]').getByTestId('state')).toHaveText('Clean');
  await expect(page.locator('tr[data-device="gw:02:00:00:00:00:0c"]').getByTestId('state')).toHaveText('Idle');
  await expect(page.locator('tr[data-device="02:00:00:00:00:0d"]').getByTestId('state')).toHaveText('No traffic');
  await expect(page.getByTestId('lease-note')).toBeHidden();
  await expect(page.getByTestId('history')).toBeHidden();
  await expect(page.getByTestId('devices-status')).toContainText('Updated');
});

test('search and state filters narrow the list', async ({ page }) => {
  await open(page, fleet());
  const rows = page.getByTestId('devices').locator('tr');
  await page.getByTestId('device-search').fill('192.168.50.101');
  await expect(rows).toHaveCount(1);
  await expect(rows.first().getByTestId('name')).toHaveText('MacBookAir');

  await page.getByTestId('device-search').fill('');
  await page.getByRole('radio', { name: /Alerting/ }).click();
  await expect(rows).toHaveCount(1);
  await page.getByRole('radio', { name: /Idle/ }).click();
  await expect(rows).toHaveCount(2);

  await page.getByTestId('device-search').fill('nothing-like-this');
  await expect(page.getByTestId('devices-filtered')).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(rows).toHaveCount(4);
});

test('clicking a device opens its incident history and an incident its transitions', async ({ page }) => {
  await open(page, [device()]);
  await page.locator('tr[data-device="gw:02:00:00:00:00:0a"]').click();
  const drawer = page.getByTestId('history');
  await expect(drawer).toBeVisible();
  await expect(page.getByTestId('history-title')).toHaveText('zero');
  const history = page.getByTestId('history-rows').locator('li[data-incident]');
  await expect(history).toHaveCount(2);
  await expect(history.nth(0)).toContainText('Ongoing');
  await expect(history.nth(1)).toContainText('Resolved');
  await expect(page).toHaveURL(/device=gw%3A02%3A00%3A00%3A00%3A00%3A0a/);

  await history.nth(1).getByRole('button').click();
  const timeline = page.getByTestId('timeline');
  await expect(timeline.locator('li')).toHaveCount(3);
  await expect(timeline).toContainText('Opened');
  await expect(timeline).toContainText('Resolved');

  await page.keyboard.press('Escape');
  await expect(drawer).toBeHidden();
  await expect(page).not.toHaveURL(/device=/);
});

test('a device link opens straight into its history', async ({ page }) => {
  await mockFeed(page);
  await mockDevices(page, [device()]);
  await page.route('**/api/v2/incidents?**', (route) => route.fulfill({ json: { cursor: 0, incidents: [] } }));
  await page.goto('/?device=gw%3A02%3A00%3A00%3A00%3A00%3A0a');
  await expect(page.getByTestId('history-title')).toHaveText('zero');
  await expect(page.getByTestId('history-empty')).toBeVisible();
  await page.goBack().catch(() => {});
});

test('without leases devices are listed by MAC with a note', async ({ page }) => {
  await open(page, [device({ hostname: null, ip: null })], {
    available: false,
    error: 'gateway-agent unavailable: ConnectError',
  });
  await expect(page.getByTestId('lease-note')).toContainText('gateway-agent unavailable');
  await expect(page.locator('tr[data-device="gw:02:00:00:00:00:0a"]').getByTestId('name')).toHaveText(
    'Unknown device',
  );
});

test('an empty network shows a friendly empty state', async ({ page }) => {
  await open(page, []);
  await expect(page.getByTestId('devices-empty')).toBeVisible();
});

test('the device panel charts its traffic with the threshold and a tooltip', async ({ page }) => {
  const minute = Math.floor(NOW / 60_000) * 60_000;
  const points = Array.from({ length: 30 }, (_, i) => ({
    minute_ms: minute - (29 - i) * 60_000,
    window_end_ms: minute - (29 - i) * 60_000 + 30_000,
    flows: 10 + i,
    distinct_targets: i,
    failed_targets: i < 20 ? 0 : 40,
    bytes: 1000,
  }));
  await open(page, [device()]);
  await page.route('**/api/v2/devices/*/history?**', (route) => route.fulfill({ json: { points } }));
  await page.locator('tr[data-device="gw:02:00:00:00:00:0a"]').click();

  const chart = page.getByTestId('traffic-chart');
  await expect(chart.getByRole('slider')).toHaveAttribute('aria-label', /failed targets peaked at 40/);
  await expect(chart).toContainText('opens at 20');
  const plot = chart.getByRole('slider');
  const box = (await plot.boundingBox())!;
  await plot.hover({ position: { x: box.width - 30, y: 60 } });
  await expect(page.getByTestId('traffic-tooltip')).toContainText('Failed targets');
  await plot.focus();
  await page.keyboard.press('ArrowLeft');
  await expect(plot).toHaveAttribute('aria-valuetext', /40 failed targets/);

  await chart.getByRole('radio', { name: '24 h' }).click();
  await expect(chart.getByRole('radio', { name: '24 h' })).toHaveAttribute('aria-checked', 'true');
  await chart.getByText('Show as table').click();
  await expect(chart.locator('details tbody tr')).toHaveCount(30);
});

test('explains incidents and the rule behind them', async ({ page }) => {
  await open(page, [device()]);
  await page.locator('tr[data-device="gw:02:00:00:00:00:0a"]').click();
  const first = page.getByTestId('history-rows').locator('li[data-incident]').first();
  await expect(first.getByTestId('threat')).toHaveText('Port scan');
  await expect(first.getByTestId('why')).toContainText('25 refused or unanswered targets in 60 s (opens at 20)');
  const old = page.locator('li[data-incident="i-old"]');
  await old.getByRole('button').click();
  await expect(old.getByTestId('rule-info')).toContainText('Resolves once both windows');
});

test('blocking a device asks first, then calls the API and marks it', async ({ page }) => {
  const calls: string[] = [];
  await open(page, [device()], undefined, { block: true });
  await page.route('**/api/v2/devices/*/block', (route) => {
    calls.push(`${route.request().method()} ${new URL(route.request().url()).pathname}`);
    const blocked = route.request().method() === 'POST';
    return route.fulfill({ json: { mac: '02:00:00:00:00:0a', blocked, message: blocked ? 'blocked' : 'unblocked' } });
  });
  await page.locator('tr[data-device="gw:02:00:00:00:00:0a"]').click();
  const control = page.getByTestId('block-control');
  await control.getByRole('button', { name: 'Block…' }).click();
  await expect(control.getByRole('alertdialog')).toContainText('Block zero?');
  await control.getByRole('button', { name: 'Cancel' }).click();
  expect(calls).toEqual([]);

  await control.getByRole('button', { name: 'Block…' }).click();
  await control.getByRole('button', { name: 'Block device' }).click();
  await expect(control).toContainText('Blocked');
  await expect(page.getByTestId('blocked-badge')).toBeVisible();
  expect(calls).toEqual(['POST /api/v2/devices/02%3A00%3A00%3A00%3A00%3A0a/block']);

  await control.getByRole('button', { name: 'Unblock' }).click();
  await expect(control).toContainText('Allowed');
  expect(calls[1]).toBe('DELETE /api/v2/devices/02%3A00%3A00%3A00%3A00%3A0a/block');
});

test('without blocking enabled the panel says how to turn it on', async ({ page }) => {
  await open(page, [device({ blocked: false })]);
  await page.locator('tr[data-device="gw:02:00:00:00:00:0a"]').click();
  await expect(page.getByTestId('block-control')).toContainText('Blocking is turned off');
  await expect(page.getByTestId('block-control').getByRole('button')).toHaveCount(0);
});

test('blocked devices are marked in the list', async ({ page }) => {
  await open(page, [device({ blocked: true })], undefined, { block: true });
  await expect(page.locator('tr[data-device="gw:02:00:00:00:00:0a"]').getByTestId('blocked')).toHaveText('Blocked');
});
