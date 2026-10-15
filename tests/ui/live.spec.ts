import { test, expect, Page, WebSocketRoute } from '@playwright/test';
import * as path from 'path';

// The /live page from images/incident-api, served from disk, with the
// incident WebSocket mocked (no backend needed).
const PAGE = path.resolve(__dirname, '../../images/incident-api/app/static/index.html');

type Server = { urls: string[]; sockets: WebSocketRoute[] };

function transition(id: string, revision: number, extra: Record<string, unknown> = {}) {
  return {
    incident_id: id,
    transition_id: `${id}:${revision}`,
    revision,
    status: revision === 1 ? 'OPEN' : 'UPDATE',
    severity: 'LOW',
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
  const server: Server = { urls: [], sockets: [] };
  await page.routeWebSocket('**/ws/v2/incidents**', (ws) => {
    server.urls.push(ws.url());
    server.sockets.push(ws);
    onConnect(ws, server.urls.length);
  });
  await page.route('**/live/**', (route) => route.fulfill({ path: PAGE, contentType: 'text/html' }));
  await page.goto('/live/');
  return server;
}

const send = (ws: WebSocketRoute, msg: unknown) => ws.send(JSON.stringify(msg));

test('shows one row per incident and applies only newer revisions', async ({ page }) => {
  await open(page, (ws) => {
    send(ws, { type: 'health', gateways: [] });
    send(ws, { type: 'snapshot', cursor: 2, incidents: [transition('a', 1), transition('b', 1)] });
    send(ws, { type: 'live', cursor: 2 });
    send(ws, { type: 'change', cursor: 3, transition: transition('a', 2, { severity: 'HIGH' }), committed_at_ms: 1_760_486_400_500 });
    send(ws, { type: 'change', cursor: 4, transition: transition('a', 1), committed_at_ms: 1_760_486_400_600 }); // stale
  });

  const rows = page.getByTestId('incidents').locator('tr');
  await expect(rows).toHaveCount(2);
  const a = page.locator('tr[data-incident="a"]');
  await expect(a.getByTestId('revision')).toHaveText('2');
  await expect(a.locator('.sev')).toHaveText('HIGH');
  await expect(a.getByTestId('delay')).toHaveText('500 ms');
  await expect(page.getByTestId('status')).toHaveText('live · cursor 2');
  await expect(page.getByTestId('connection-banner')).toBeHidden();
});

test('resolved incidents stay visible but move below active ones', async ({ page }) => {
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
        { gateway_id: 'gw', component: 'sensor-spool', kind: 'SPOOL_LOSS', count: 42, first_sequence: 100, last_sequence: 141, detail: 'spool full', first_event_time_ms: 1, last_event_time_ms: 2 },
        { gateway_id: 'gw', component: 'sensor', kind: 'HEARTBEAT', count: 0, first_sequence: 0, last_sequence: 0, detail: 'waiting for wlan0: That device is not up' },
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

test('a healthy gateway shows no ingestion banner', async ({ page }) => {
  await open(page, (ws) => {
    send(ws, { type: 'health', gateways: [{ gateway_id: 'gw', component: 'sensor', kind: 'HEARTBEAT', count: 10, first_sequence: 0, last_sequence: 0, detail: '' }] });
    send(ws, { type: 'snapshot', cursor: 0, incidents: [] });
    send(ws, { type: 'live', cursor: 0 });
  });
  await expect(page.getByTestId('status')).toHaveText('live · cursor 0');
  await expect(page.getByTestId('ingestion-banner')).toBeHidden();
});
