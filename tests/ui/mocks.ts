import type { Page, WebSocketRoute } from '@playwright/test';

// Shared API mocks for the incident-api UI.

export type Server = { urls: string[]; sockets: WebSocketRoute[] };

export const send = (ws: WebSocketRoute, msg: unknown) => ws.send(JSON.stringify(msg));

/** Mocks the incident WebSocket; onConnect runs for every (re)connect. */
export async function mockFeed(
  page: Page,
  onConnect: (ws: WebSocketRoute, n: number) => void = () => {},
): Promise<Server> {
  const server: Server = { urls: [], sockets: [] };
  await page.routeWebSocket('**/ws/v2/incidents**', (ws) => {
    server.urls.push(ws.url());
    server.sockets.push(ws);
    onConnect(ws, server.urls.length);
  });
  return server;
}

/** Mocks GET /api/v2/devices (empty unless given). */
export async function mockDevices(
  page: Page,
  devices: unknown[] = [],
  leases = { available: true, error: null as string | null },
) {
  await page.route('**/api/v2/devices', (route) =>
    route.fulfill({ json: { devices, leases, generated_at_ms: Date.now() } }),
  );
}
