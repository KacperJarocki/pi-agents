import { test, expect, APIRequestContext } from '@playwright/test';

function uniqueDevicePayload() {
  const hex = Math.random().toString(16).slice(2, 8);
  return {
    mac_address: `02:00:00:${hex.slice(0, 2)}:${hex.slice(2, 4)}:${hex.slice(4, 6)}`,
    ip_address: `192.168.1.${Math.floor(Math.random() * 200) + 20}`,
    hostname: `e2e-device-${hex}`,
    device_type: 'integration-test',
  };
}

async function createDevice(request: APIRequestContext) {
  const payload = uniqueDevicePayload();
  const response = await request.post('http://localhost:8080/api/v1/devices', {
    data: payload,
  });

  expect(response.ok()).toBeTruthy();
  const body = await response.json();
  return { payload, body };
}

async function expectDeviceInList(request: APIRequestContext, deviceId: number, hostname: string) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const response = await request.get('/api/devices');
    expect(response.ok()).toBeTruthy();

    const body = await response.json();
    const found = (body.devices || []).some((device: { id?: number; hostname?: string }) => (
      device.id === deviceId && device.hostname === hostname
    ));

    if (found) {
      expect(body).toEqual(
        expect.objectContaining({
          total: expect.any(Number),
          devices: expect.arrayContaining([
            expect.objectContaining({
              id: deviceId,
              hostname,
            }),
          ]),
        })
      );
      return;
    }

    await new Promise(resolve => setTimeout(resolve, 250));
  }

  throw new Error(`Device ${deviceId} (${hostname}) did not appear in /api/devices after retries`);
}

test.describe('Integration API', () => {
  test('gateway-api health responds successfully', async ({ request }) => {
    const response = await request.get('http://localhost:8080/health');

    expect(response.ok()).toBeTruthy();
    await expect(response.json()).resolves.toMatchObject({ status: 'healthy' });
  });

  test('dashboard proxy metrics and alerts endpoints respond', async ({ request }) => {
    const metricsResponse = await request.get('/api/metrics/summary');
    expect(metricsResponse.ok()).toBeTruthy();
    await expect(metricsResponse.json()).resolves.toEqual(
      expect.objectContaining({
        total_devices: expect.any(Number),
        active_devices: expect.any(Number),
        total_alerts_24h: expect.any(Number),
      })
    );

    const alertsResponse = await request.get('/api/alerts');
    expect(alertsResponse.ok()).toBeTruthy();
    await expect(alertsResponse.json()).resolves.toEqual(
      expect.objectContaining({
        alerts: expect.any(Array),
        total: expect.any(Number),
      })
    );
  });

  test('can create and fetch a device through gateway-api and dashboard proxy', async ({ request }) => {
    const { payload, body } = await createDevice(request);

    expect(body).toEqual(
      expect.objectContaining({
        id: expect.any(Number),
        mac_address: payload.mac_address,
        ip_address: payload.ip_address,
        hostname: payload.hostname,
      })
    );

    const gatewayResponse = await request.get(`http://localhost:8080/api/v1/devices/${body.id}`);
    expect(gatewayResponse.ok()).toBeTruthy();
    await expect(gatewayResponse.json()).resolves.toEqual(
      expect.objectContaining({
        id: body.id,
        mac_address: payload.mac_address,
      })
    );

    const proxyResponse = await request.get(`/api/devices/${body.id}`);
    expect(proxyResponse.ok()).toBeTruthy();
    await expect(proxyResponse.json()).resolves.toEqual(
      expect.objectContaining({
        id: body.id,
        hostname: payload.hostname,
      })
    );

    await expectDeviceInList(request, body.id, payload.hostname);
  });
});
