import { test, expect } from '@playwright/test';
import { setupMocks } from './fixtures/api-mock';

test.describe('Gateway WiFi page — /gateway', () => {
  test.beforeEach(async ({ page }) => {
    await setupMocks(page);
    await page.goto('/gateway');
  });

  test('page title and nav render', async ({ page }) => {
    await expect(page).toHaveTitle('Gateway WiFi Settings');
    await expect(page.getByRole('heading', { name: 'WiFi AP Configuration' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Back to dashboard' })).toBeVisible();
  });

  test('form fields are pre-populated with current config', async ({ page }) => {
    // Labels are not linked via for/id — target inputs by name attribute
    await expect(page.locator('input[name="ssid"]')).toHaveValue('IoT-Security');
    await expect(page.locator('input[name="psk"]')).toHaveValue('change-me-please');
    await expect(page.locator('input[name="country_code"]')).toHaveValue('PL');
    await expect(page.locator('input[name="channel"]')).toHaveValue('6');
  });

  test('network interface fields visible', async ({ page }) => {
    await expect(page.locator('input[name="ap_interface"]')).toHaveValue('wlan0');
    await expect(page.locator('input[name="upstream_interface"]')).toHaveValue('eth0');
  });

  test('Enabled checkbox is checked by default', async ({ page }) => {
    await expect(page.getByRole('checkbox', { name: /Enabled/i })).toBeChecked();
  });

  test('four action buttons are present', async ({ page }) => {
    await expect(page.getByRole('button', { name: 'Validate' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Apply' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Rollback' })).toBeVisible();
  });

  test('Agent Status panel is visible', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Agent Status' })).toBeVisible();
    await expect(page.getByText('AP iface exists')).toBeVisible();
    await expect(page.getByText('IP forward')).toBeVisible();
  });

  test('Validate button submits form and shows result', async ({ page }) => {
    // The dashboard's POST /gateway/validate proxies to gateway-agent which is offline in tests.
    // We intercept the POST and return a mocked response.
    // Since this is a full HTML form POST (not fetch), we intercept at the route level.
    await page.route('**/gateway/validate', route => {
      return route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: `<html><head><title>Gateway WiFi Settings</title></head><body>
          <div class="alert alert-success">Configuration is valid</div>
        </body></html>`,
      });
    });

    await page.getByRole('button', { name: 'Validate' }).click();
    await expect(page.getByText('Configuration is valid')).toBeVisible();
  });

  test('Save button submits form', async ({ page }) => {
    await page.route('**/gateway/save', route => {
      return route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: `<html><head><title>Gateway WiFi Settings</title></head><body>
          <div class="alert alert-success">Configuration saved</div>
        </body></html>`,
      });
    });

    await page.getByRole('button', { name: 'Save' }).click();
    await expect(page.getByText('Configuration saved')).toBeVisible();
  });

  test('Apply button submits form', async ({ page }) => {
    await page.route('**/gateway/apply', route => {
      return route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: `<html><head><title>Gateway WiFi Settings</title></head><body>
          <div class="alert alert-success">Configuration applied</div>
        </body></html>`,
      });
    });

    await page.getByRole('button', { name: 'Apply' }).click();
    await expect(page.getByText('Configuration applied')).toBeVisible();
  });

  test('Rollback button submits form', async ({ page }) => {
    await page.route('**/gateway/rollback', route => {
      return route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: `<html><head><title>Gateway WiFi Settings</title></head><body>
          <div class="alert alert-info">Configuration rolled back</div>
        </body></html>`,
      });
    });

    await page.getByRole('button', { name: 'Rollback' }).click();
    await expect(page.getByText('Configuration rolled back')).toBeVisible();
  });
});
