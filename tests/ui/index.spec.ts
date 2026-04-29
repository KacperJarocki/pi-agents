import { test, expect } from '@playwright/test';
import { setupMocks } from './fixtures/api-mock';
import { sampleMetricsSummary } from './fixtures/sample-data';

test.describe('Index page — /  ', () => {
  test.beforeEach(async ({ page }) => {
    await setupMocks(page);
    await page.goto('/');
  });

  test('page title and nav render', async ({ page }) => {
    await expect(page).toHaveTitle('IoT Security Dashboard');
    await expect(page.getByRole('heading', { name: 'IoT Security Dashboard' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Gateway WiFi' })).toBeVisible();
  });

  test('metric stat cards populate from /api/metrics/summary', async ({ page }) => {
    // Metrics are fetched by JS on load — target specific stat card IDs to avoid strict mode violations
    await expect(page.locator('#metric-devices')).toHaveText(String(sampleMetricsSummary.total_devices));
    await expect(page.locator('#metric-active')).toHaveText(String(sampleMetricsSummary.active_devices));
    await expect(page.locator('#metric-alerts')).toHaveText(String(sampleMetricsSummary.total_alerts_24h));
    await expect(page.locator('#metric-critical')).toHaveText(String(sampleMetricsSummary.critical_anomalies));
  });

  test('alert breakdown shows anomaly + behavior counts', async ({ page }) => {
    const breakdown = `${sampleMetricsSummary.total_anomalies_24h} anomaly + ${sampleMetricsSummary.behavior_alerts_24h} behavior`;
    await expect(page.getByText(breakdown)).toBeVisible();
  });

  test('devices tab shows device card with hostname', async ({ page }) => {
    // Devices tab is active by default — HTMX partial /partial/devices loads cards
    await expect(page.getByRole('tab', { name: 'Devices' })).toBeVisible();
    // Device card links to /devices/1
    await expect(page.getByRole('link', { name: /test-device/ })).toBeVisible();
  });

  test('tab switching — Timeline tab makes HTMX partial visible', async ({ page }) => {
    await page.getByRole('tab', { name: 'Timeline' }).click();
    // Alpine.js tabs use class "tab-active", not aria-selected
    await expect(page.getByRole('tab', { name: 'Timeline' })).toHaveClass(/tab-active/);
  });

  test('tab switching — Top Talkers tab', async ({ page }) => {
    await page.getByRole('tab', { name: 'Top Talkers' }).click();
    await expect(page.getByRole('tab', { name: 'Top Talkers' })).toHaveClass(/tab-active/);
  });

  test('Recent Alerts section visible with source filter dropdown', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Recent Alerts' })).toBeVisible();
    const dropdown = page.getByRole('combobox').first();
    await expect(dropdown).toBeVisible();
    await expect(dropdown).toContainText('All Sources');
  });

  test('alert source filter changes selection', async ({ page }) => {
    const dropdown = page.getByRole('combobox').first();
    await dropdown.selectOption('Anomaly Detection');
    await expect(dropdown).toHaveValue('anomaly');
  });

  test('Alert Timeline heading visible', async ({ page }) => {
    await expect(page.getByRole('heading', { name: 'Alert Timeline' })).toBeVisible();
  });
});
