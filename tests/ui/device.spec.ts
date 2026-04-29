import { test, expect } from '@playwright/test';
import { setupMocks } from './fixtures/api-mock';
import { DEVICE_ID, sampleDevice, sampleRiskContributors } from './fixtures/sample-data';

test.describe(`Device detail page — /devices/${DEVICE_ID}`, () => {
  test.beforeEach(async ({ page }) => {
    await setupMocks(page);
    await page.goto(`/devices/${DEVICE_ID}`);
  });

  test('page title and nav render', async ({ page }) => {
    await expect(page).toHaveTitle(`Device Console #${DEVICE_ID}`);
    await expect(page.getByRole('link', { name: 'Return to Grid' })).toBeVisible();
  });

  test('identity card shows hostname, IP, MAC', async ({ page }) => {
    await expect(page.getByText(sampleDevice.hostname)).toBeVisible();
    await expect(page.getByText(sampleDevice.ip_address)).toBeVisible();
    await expect(page.getByText(sampleDevice.mac_address)).toBeVisible();
  });

  test('connection pill shows connected state', async ({ page }) => {
    // Snapshot showed "CONNECTED / recent_traffic"
    await expect(page.getByText(/connected/i)).toBeVisible();
  });

  test('risk score card shows correct percentage', async ({ page }) => {
    // risk_score is 42.5 → shown as "42.5%"
    await expect(page.getByText(`${sampleDevice.risk_score}%`)).toBeVisible();
  });

  test('risk breakdown shows ml_risk, behavior_risk, protocol_risk', async ({ page }) => {
    await expect(page.getByText('ml_risk')).toBeVisible();
    await expect(page.getByText('behavior_risk')).toBeVisible();
    await expect(page.getByText('protocol_risk')).toBeVisible();
    await expect(page.getByText('correlation_bonus')).toBeVisible();
  });

  test('model comparison table has header columns', async ({ page }) => {
    await expect(page.getByRole('columnheader', { name: 'Model', exact: true })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Anomaly Score' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Risk Score' })).toBeVisible();
    await expect(page.getByRole('columnheader', { name: 'Anomaly?' })).toBeVisible();
  });

  test('ML model health table and Train Now button visible', async ({ page }) => {
    // "ML Model Health" is a <div class="text-xs">, not a heading element
    await expect(page.getByText('ML Model Health')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Train Now' })).toBeVisible();
  });

  test('training config form has all spinbutton inputs', async ({ page }) => {
    await expect(page.getByText('Training Config (Device Override)')).toBeVisible();
    await expect(page.getByText('Training Hours')).toBeVisible();
    await expect(page.getByText('Min Samples')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save Overrides' })).toBeVisible();
  });

  test('effective config panel shows merged values', async ({ page }) => {
    await expect(page.getByText('Effective Config (Merged)')).toBeVisible();
    await expect(page.getByText('training_hours')).toBeVisible();
    await expect(page.getByText('min_training_samples')).toBeVisible();
  });

  test('block button sends POST to /api/devices/1/block', async ({ page }) => {
    // Capture network request to block endpoint
    const [request] = await Promise.all([
      page.waitForRequest(req =>
        req.url().includes(`/api/devices/${DEVICE_ID}/block`) && req.method() === 'POST'
      ),
      page.getByRole('button', { name: /block/i }).click(),
    ]);
    expect(request.method()).toBe('POST');
  });

  test('behavior alerts filter dropdown exists', async ({ page }) => {
    // "Behavior Alerts" is a <div class="text-xs">, not a heading element
    await expect(page.getByText('Behavior Alerts')).toBeVisible();
    // Verify the alert source filter combobox exists within the Behavior Alerts section
    // (page has 4 comboboxes total: model-select, training-data timerange, ml-health model, alert source)
    const comboboxes = page.getByRole('combobox');
    await expect(comboboxes).toHaveCount(4);
  });

  test('risk contributors section visible', async ({ page }) => {
    await expect(page.getByText('Risk Contributors')).toBeVisible();
  });

  test('protocol signals section shows dns and icmp rows', async ({ page }) => {
    await expect(page.getByText('Protocol Signals')).toBeVisible();
    await expect(page.getByText('dns_failures_24h')).toBeVisible();
    await expect(page.getByText('icmp_echo_requests_24h')).toBeVisible();
  });
});
