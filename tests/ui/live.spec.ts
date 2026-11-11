import { test, expect, Page, WebSocketRoute } from '@playwright/test';
import { mockDevices, mockFeed, send, Server } from './mocks';

// The /live page of images/incident-api/ui with the incident WebSocket mocked.

function transition(id: string, revision: number, extra: Record<string, unknown> = {}) {
  return {
    incident_id: id,
    transition_id: `${id}:${revision}`,
    revision,
    status: revision === 1 ? 'OPEN' : 'UPDATE',
    severity: 'LOW',
    rule_id: 'port-scan',
    threat_family: 'PORT_SCAN',
    device_key: 'gw:02:00:00:00:00:0a',
    horizons: [
      { horizon_seconds: 60, value: 20, open_threshold: 20, over_threshold: true },
      { horizon_seconds: 900, value: 20, open_threshold: 40, over_threshold: false },
    ],
    episode_start_ms: 1_760_486_400_000,
    evidence_event_time_ms: 1_760_486_400_000,
    detected_at_ms: 1_760_486_400_120,
    committed_at_ms: 1_760_486_400_350,
    ...extra,
  };
}

async function open(page: Page, onConnect: (ws: WebSocketRoute, n: number) => void): Promise<Server> {
  await mockDevices(page);
  const server = await mockFeed(page, onConnect);
  await page.goto('/live/');
  return server;
}

test('shows one row per incident and applies only newer revisions', async ({ page }) => {
  await open(page, (ws) => {
    send(ws, { type: 'health', gateways: [] });
    send(ws, { type: 'snapshot', cursor: 2, incidents: [transition('a', 1), transition('b', 1)] });
    send(ws, { type: 'live', cursor: 2 });
    send(ws, {
      type: 'change',
      cursor: 3,
      transition: transition('a', 2, { severity: 'HIGH' }),
      committed_at_ms: 1_760_486_400_500,
    });
    send(ws, { type: 'change', cursor: 4, transition: transition('a', 1), committed_at_ms: 1_760_486_400_600 }); // stale
  });

  const rows = page.getByTestId('incidents').locator('tr');
  await expect(rows).toHaveCount(2);
  const a = page.locator('tr[data-incident="a"]');
  await expect(a.getByTestId('revision')).toHaveText('2');
  await expect(a.getByTestId('severity')).toHaveText('High');
  await expect(a.getByTestId('delay')).toHaveText('500 ms');
  await expect(page.getByTestId('status')).toHaveText('Live');
  await expect(page.getByTestId('status')).toHaveAttribute('data-cursor', '4');
  await expect(page.getByTestId('open-count')).toContainText('2');
  await expect(page.getByTestId('nav-active-count')).toHaveText('2');
  await expect(page).toHaveTitle('(2) Live incidents · pi-agents');
  await expect(page.getByTestId('connection-banner')).toBeHidden();
});

test('resolved incidents stay visible but move below active ones, and can be hidden', async ({ page }) => {
  await open(page, (ws) => {
    send(ws, { type: 'snapshot', cursor: 1, incidents: [transition('old', 1)] });
    send(ws, { type: 'live', cursor: 1 });
    send(ws, { type: 'change', cursor: 2, transition: transition('old', 2, { status: 'RESOLVE' }) });
    send(ws, { type: 'change', cursor: 3, transition: transition('new', 1, { detected_at_ms: 1 }) });
  });
  const rows = page.getByTestId('incidents').locator('tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toHaveAttribute('data-incident', 'new');
  await expect(rows.nth(1)).toHaveClass(/resolved/);
  await expect(rows.nth(1).getByTestId('incident-status')).toHaveText('Resolved');

  await page.getByText('Show resolved').click();
  await expect(rows).toHaveCount(1);
  await page.reload();
  await expect(page.getByTestId('show-resolved')).not.toBeChecked();
  await page.getByTestId('show-resolved').check();
});

test('filters incidents by severity', async ({ page }) => {
  await open(page, (ws) => {
    send(ws, {
      type: 'snapshot',
      cursor: 2,
      incidents: [transition('low', 1), transition('high', 1, { severity: 'HIGH' })],
    });
    send(ws, { type: 'live', cursor: 2 });
  });
  const rows = page.getByTestId('incidents').locator('tr');
  await expect(rows).toHaveCount(2);
  await page.getByRole('radio', { name: /High/ }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toHaveAttribute('data-incident', 'high');
  await page.getByRole('radio', { name: /Medium/ }).click();
  await expect(page.getByTestId('empty-filtered')).toBeVisible();
});

test('reconnects with backoff and resumes after the last cursor', async ({ page }) => {
  const server = await open(page, (ws, n) => {
    if (n === 1) {
      send(ws, { type: 'snapshot', cursor: 5, incidents: [transition('a', 1)] });
      send(ws, { type: 'live', cursor: 5 });
      send(ws, { type: 'change', cursor: 6, transition: transition('a', 2) });
    } else {
      send(ws, { type: 'change', cursor: 7, transition: transition('a', 3) });
      send(ws, { type: 'live', cursor: 7 });
    }
  });

  await expect(page.locator('tr[data-incident="a"]').getByTestId('revision')).toHaveText('2');
  await server.sockets[0].close();
  await expect(page.getByTestId('connection-banner')).toContainText('Live connection lost');
  await expect(page.getByTestId('status')).toHaveText('Offline');

  await expect.poll(() => server.urls.length, { timeout: 5_000 }).toBe(2);
  expect(server.urls[0]).not.toContain('after=');
  expect(new URL(server.urls[1]).searchParams.get('after')).toBe('6');
  await expect(page.getByTestId('connection-banner')).toBeHidden();
  await expect(page.locator('tr[data-incident="a"]').getByTestId('revision')).toHaveText('3');
});

test('an expired cursor resets the table to the new snapshot', async ({ page }) => {
  await open(page, (ws) => {
    send(ws, { type: 'snapshot', cursor: 1, incidents: [transition('gone', 1)] });
    send(ws, { type: 'reset', reason: 'cursor expired' });
    send(ws, { type: 'snapshot', cursor: 9, incidents: [transition('kept', 4)] });
    send(ws, { type: 'live', cursor: 9 });
  });
  const rows = page.getByTestId('incidents').locator('tr');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toHaveAttribute('data-incident', 'kept');
});

test('shows ingestion loss and capture problems from gateway health', async ({ page }) => {
  await open(page, (ws) => {
    send(ws, {
      type: 'health',
      gateways: [
        {
          gateway_id: 'gw',
          component: 'sensor-spool',
          kind: 'SPOOL_LOSS',
          count: 42,
          first_sequence: 100,
          last_sequence: 141,
          detail: 'spool full',
          first_event_time_ms: 1,
          last_event_time_ms: 2,
        },
        {
          gateway_id: 'gw',
          component: 'sensor',
          kind: 'HEARTBEAT',
          count: 0,
          first_sequence: 0,
          last_sequence: 0,
          detail: 'waiting for wlan0: That device is not up',
        },
      ],
    });
    send(ws, { type: 'snapshot', cursor: 0, incidents: [] });
    send(ws, { type: 'live', cursor: 0 });
  });
  const banner = page.getByTestId('ingestion-banner');
  await expect(banner).toBeVisible();
  await expect(banner).toContainText('lost 42 events');
  await expect(banner).toContainText('waiting for wlan0');
  await expect(page.getByTestId('empty')).toBeVisible();
});

test('a few frames without a lease or old count problems do not raise the banner', async ({ page }) => {
  const now = Date.now();
  await open(page, (ws) => {
    send(ws, {
      type: 'health',
      gateways: [
        {
          gateway_id: 'gw',
          component: 'sensor',
          kind: 'UNATTRIBUTED',
          count: 1,
          first_sequence: 0,
          last_sequence: 0,
          detail: '',
          first_event_time_ms: now - 5_000,
          last_event_time_ms: now,
        },
        {
          gateway_id: 'gw',
          component: 'sensor',
          kind: 'FLOW_TABLE_EVICTION',
          count: 9,
          first_sequence: 0,
          last_sequence: 0,
          detail: '',
          first_event_time_ms: now - 7_200_000,
          last_event_time_ms: now - 3_600_000,
        },
      ],
    });
    send(ws, { type: 'snapshot', cursor: 0, incidents: [] });
    send(ws, { type: 'live', cursor: 0 });
  });
  await expect(page.getByTestId('status')).toHaveText('Live');
  await expect(page.getByTestId('ingestion-banner')).toBeHidden();
});

test('a burst of unattributed frames raises the banner', async ({ page }) => {
  const now = Date.now();
  await open(page, (ws) => {
    send(ws, {
      type: 'health',
      gateways: [
        {
          gateway_id: 'gw',
          component: 'sensor',
          kind: 'UNATTRIBUTED',
          count: 250,
          first_sequence: 0,
          last_sequence: 0,
          detail: '',
          first_event_time_ms: now - 5_000,
          last_event_time_ms: now,
        },
      ],
    });
    send(ws, { type: 'snapshot', cursor: 0, incidents: [] });
    send(ws, { type: 'live', cursor: 0 });
  });
  await expect(page.getByTestId('ingestion-banner')).toContainText('250 frames from devices without a DHCP lease');
});

test('shows the device hostname and opens its history in the app', async ({ page }) => {
  await mockDevices(page, [
    {
      mac: '02:00:00:00:00:0a',
      device_key: 'gw:02:00:00:00:00:0a',
      hostname: 'zero',
      ip: '192.168.50.134',
      last_seen_ms: Date.now(),
      features: {},
      incidents: { active: 1, total: 1, severity: 'LOW' },
    },
  ]);
  await page.route('**/api/v2/incidents?**', (route) => route.fulfill({ json: { cursor: 1, incidents: [] } }));
  const server = await mockFeed(page, (ws) => {
    send(ws, { type: 'snapshot', cursor: 1, incidents: [transition('a', 1)] });
    send(ws, { type: 'live', cursor: 1 });
  });
  await page.goto('/live/');

  const link = page.locator('tr[data-incident="a"]').getByTestId('device');
  await expect(link).toHaveText('zero');
  await expect(link).toHaveAttribute('href', '/?device=gw%3A02%3A00%3A00%3A00%3A00%3A0a');
  await expect(page.locator('tr[data-incident="a"]')).toContainText('192.168.50.134');

  await link.click();
  await expect(page).toHaveURL(/\/\?device=gw%3A02/);
  await expect(page.getByTestId('history-title')).toHaveText('zero');
  expect(server.urls).toHaveLength(1); // client-side navigation keeps the socket
});

test('a healthy gateway shows no ingestion banner', async ({ page }) => {
  await open(page, (ws) => {
    send(ws, {
      type: 'health',
      gateways: [
        {
          gateway_id: 'gw',
          component: 'sensor',
          kind: 'HEARTBEAT',
          count: 10,
          first_sequence: 0,
          last_sequence: 0,
          detail: '',
        },
      ],
    });
    send(ws, { type: 'snapshot', cursor: 0, incidents: [] });
    send(ws, { type: 'live', cursor: 0 });
  });
  await expect(page.getByTestId('status')).toHaveText('Live');
  await expect(page.getByTestId('ingestion-banner')).toBeHidden();
});

test('explains each incident in plain words', async ({ page }) => {
  await open(page, (ws) => {
    send(ws, { type: 'snapshot', cursor: 1, incidents: [transition('a', 1)] });
    send(ws, { type: 'live', cursor: 1 });
  });
  const row = page.locator('tr[data-incident="a"]');
  await expect(row.getByTestId('threat')).toHaveText('Port scan');
  await expect(row.getByTestId('why')).toHaveText('20 refused or unanswered targets in 60 s (opens at 20)');
});
