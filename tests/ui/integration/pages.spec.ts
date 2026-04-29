import { test, expect, APIRequestContext } from '@playwright/test';

function uniqueDevicePayload() {
  const hex = Math.random().toString(16).slice(2, 8);
  const octet = Math.floor(Math.random() * 200) + 20;
  return {
    mac_address: `02:11:22:${hex.slice(0, 2)}:${hex.slice(2, 4)}:${hex.slice(4, 6)}`,
    ip_address: `192.168.50.${octet}`,
    hostname: `ui-e2e-${hex}`,
    device_type: 'ui-integration-test',
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

test.describe('Integration pages', () => {
  test('dashboard home renders against the live stack', async ({ page }) => {
    await page.goto('/');

    await expect(page).toHaveTitle('IoT Security Dashboard');
    await expect(page.getByRole('heading', { name: 'IoT Security Dashboard' })).toBeVisible();
    await expect(page.locator('#metric-devices')).toHaveText(/\d+/);
    await expect(page.locator('#metric-alerts')).toHaveText(/\d+/);
  });

  test('created device loads on its detail page through the live stack', async ({ request, page }) => {
    const { payload, body } = await createDevice(request);

    await page.goto(`/devices/${body.id}`);
    await expect(page).toHaveTitle(`Device Console #${body.id}`);
    await expect(page.getByText(payload.hostname)).toBeVisible();
    await expect(page.getByText(payload.ip_address)).toBeVisible();
    await expect(page.getByText(payload.mac_address)).toBeVisible();
  });

  test('gateway page renders persisted default config without hardware agent', async ({ page }) => {
    await page.goto('/gateway');

    await expect(page).toHaveTitle('Gateway WiFi Settings');
    await expect(page.locator('input[name="ssid"]')).toHaveValue('IoT-Security');
    await expect(page.locator('input[name="psk"]')).toHaveValue('change-me-please');
    await expect(page.locator('input[name="country_code"]')).toHaveValue('PL');
    await expect(page.locator('input[name="channel"]')).toHaveValue('6');
  });
});
