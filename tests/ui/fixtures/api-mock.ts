import { Page } from '@playwright/test';
import {
  DEVICE_ID,
  sampleAlerts,
  sampleDeviceList,
  sampleDevice,
  sampleMetricsSummary,
  sampleModelScores,
  sampleRiskContributors,
  sampleTrainingConfig,
  sampleMlStatus,
  sampleGatewayWifiConfig,
  sampleGatewayStatus,
} from './sample-data';

// All routes are the dashboard's own proxy endpoints (localhost:3000/api/* and /partial/*),
// NOT the gateway-api directly. The dashboard proxies everything via its internal httpx client.

export async function setupMocks(page: Page) {
  // ── Metrics ────────────────────────────────────────────────────────────────
  await page.route('**/api/metrics/summary', route =>
    route.fulfill({ json: sampleMetricsSummary })
  );
  await page.route('**/api/metrics/timeline', route =>
    route.fulfill({ json: { timeline: [] } })
  );
  await page.route('**/api/metrics/top-talking', route =>
    route.fulfill({ json: { devices: [] } })
  );
  await page.route('**/api/metrics/ml-status', route =>
    route.fulfill({ json: sampleMlStatus })
  );

  // ── Devices ────────────────────────────────────────────────────────────────
  await page.route('**/api/devices', route =>
    route.fulfill({ json: sampleDeviceList })
  );
  await page.route(`**/api/devices/${DEVICE_ID}`, route =>
    route.fulfill({ json: sampleDevice })
  );
  await page.route(`**/api/devices/${DEVICE_ID}/traffic`, route =>
    route.fulfill({ json: { flows: [] } })
  );
  await page.route(`**/api/devices/${DEVICE_ID}/destinations`, route =>
    route.fulfill({ json: { destinations: [], ports: [], dns_queries: [] } })
  );
  await page.route(`**/api/devices/${DEVICE_ID}/anomalies`, route =>
    route.fulfill({ json: { total: 0, anomalies: [] } })
  );
  await page.route(`**/api/devices/${DEVICE_ID}/inference-history`, route =>
    route.fulfill({ json: { history: [] } })
  );
  await page.route(`**/api/devices/${DEVICE_ID}/behavior-alerts`, route =>
    route.fulfill({ json: { total: 0, alerts: [] } })
  );
  await page.route(`**/api/devices/${DEVICE_ID}/risk-contributors`, route =>
    route.fulfill({ json: sampleRiskContributors })
  );
  await page.route(`**/api/devices/${DEVICE_ID}/behavior-baseline`, route =>
    route.fulfill({ json: { baseline: {} } })
  );
  await page.route(`**/api/devices/${DEVICE_ID}/protocol-signals`, route =>
    route.fulfill({
      json: {
        dns_failures_24h: 0,
        icmp_echo_requests_24h: 0,
        unique_destinations: 0,
      },
    })
  );
  await page.route(`**/api/devices/${DEVICE_ID}/model-config`, route =>
    route.fulfill({ json: { active_model: 'isolation_forest', available_models: ['isolation_forest', 'lof', 'ocsvm', 'autoencoder'] } })
  );
  await page.route(`**/api/devices/${DEVICE_ID}/model-scores**`, route =>
    route.fulfill({ json: sampleModelScores })
  );

  // Block/unblock
  await page.route(`**/api/devices/${DEVICE_ID}/block`, route => {
    if (route.request().method() === 'POST') {
      return route.fulfill({ status: 200, json: { status: 'blocked' } });
    }
    return route.fulfill({ status: 200, json: { status: 'unblocked' } });
  });

  // ── Alerts ─────────────────────────────────────────────────────────────────
  await page.route('**/api/alerts**', route =>
    route.fulfill({ json: sampleAlerts })
  );
  await page.route('**/api/anomalies**', route =>
    route.fulfill({ json: { total: 0, anomalies: [] } })
  );

  // ── ML training config ─────────────────────────────────────────────────────
  await page.route('**/api/ml/config', route =>
    route.fulfill({ json: sampleTrainingConfig })
  );
  await page.route(`**/api/ml/devices/${DEVICE_ID}/training-config`, route => {
    const method = route.request().method();
    if (method === 'PUT') {
      return route.fulfill({ status: 200, json: { ...sampleTrainingConfig, has_overrides: true } });
    }
    if (method === 'DELETE') {
      return route.fulfill({ status: 200, json: sampleTrainingConfig });
    }
    return route.fulfill({ json: sampleTrainingConfig });
  });
  await page.route(`**/api/ml/devices/${DEVICE_ID}/training-data**`, route =>
    route.fulfill({ json: { total_flows: 0, feature_buckets: 0, trained_models: 0, buckets: [] } })
  );
  await page.route(`**/api/ml/devices/${DEVICE_ID}/raw-flows**`, route =>
    route.fulfill({ json: { total: 0, flows: [] } })
  );
  await page.route(`**/api/ml/devices/${DEVICE_ID}/train`, route =>
    route.fulfill({ status: 200, json: { job_name: 'train-job-abc123', status: 'started' } })
  );
  await page.route(`**/api/ml/devices/${DEVICE_ID}/train/status`, route =>
    route.fulfill({ json: { status: 'not_found' } })
  );

  // ── Partials (HTMX server-rendered fragments) ──────────────────────────────
  // We do NOT mock these — they are rendered server-side by the dashboard itself
  // and already call the mocked /api/* endpoints above. Let them pass through.

  // ── Gateway WiFi (dashboard form POSTs — not proxy) ────────────────────────
  // gateway.html form actions POST directly to /gateway/validate etc.
  // These are handled by the dashboard's own FastAPI routes (not mocked here).
}
