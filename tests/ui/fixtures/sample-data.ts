// Realistic payloads matching actual API response shapes from gateway-api.
// Used by api-mock.ts to stub all /api/* routes on the dashboard proxy.

export const DEVICE_ID = 1;

export const sampleDevice = {
  id: DEVICE_ID,
  mac_address: 'aa:bb:cc:dd:ee:ff',
  ip_address: '192.168.50.101',
  hostname: 'test-device',
  device_type: null,
  first_seen: '2026-04-01T10:00:00',
  last_seen: '2026-04-29T06:00:00',
  is_active: true,
  risk_score: 42.5,
  last_inference_score: -0.12,
  last_inference_at: '2026-04-29T05:55:00',
  extra_data: null,
  connected: true,
  connection_source: 'recent_traffic',
  model_status: 'trained',
};

export const sampleDeviceList = {
  total: 1,
  devices: [sampleDevice],
};

export const sampleMetricsSummary = {
  total_devices: 1,
  active_devices: 1,
  total_anomalies_24h: 3,
  critical_anomalies: 1,
  avg_risk_score: 42.5,
  total_traffic_mb: 12.4,
  behavior_alerts_24h: 2,
  total_alerts_24h: 5,
};

export const sampleAlerts = {
  total: 2,
  alerts: [
    {
      id: 1,
      device_id: DEVICE_ID,
      timestamp: '2026-04-29T05:50:00',
      alert_type: 'isolation_forest',
      severity: 'warning',
      score: 0.65,
      description: 'Anomaly detected by Isolation Forest',
      source: 'anomaly',
    },
    {
      id: 2,
      device_id: DEVICE_ID,
      timestamp: '2026-04-29T05:40:00',
      alert_type: 'dns_burst',
      severity: 'warning',
      score: 0.55,
      description: 'Unusual DNS query burst',
      source: 'behavior',
    },
  ],
};

export const sampleModelScores = [
  {
    model_type: 'isolation_forest',
    anomaly_score: -0.12,
    risk_score: 42.5,
    is_anomaly: false,
    severity: null,
    timestamp: '2026-04-29T05:55:00',
  },
  {
    model_type: 'lof',
    anomaly_score: -0.08,
    risk_score: 38.0,
    is_anomaly: false,
    severity: null,
    timestamp: '2026-04-29T05:55:00',
  },
  {
    model_type: 'ocsvm',
    anomaly_score: -0.15,
    risk_score: 45.0,
    is_anomaly: false,
    severity: null,
    timestamp: '2026-04-29T05:55:00',
  },
  {
    model_type: 'autoencoder',
    anomaly_score: 0.22,
    risk_score: 50.0,
    is_anomaly: true,
    severity: 'warning',
    timestamp: '2026-04-29T05:55:00',
  },
];

export const sampleRiskContributors = {
  ml_risk: 18.0,
  behavior_risk: 12.0,
  protocol_risk: 5.0,
  correlation_bonus: 7.5,
  final_risk: 42.5,
  previous_risk: 40.0,
  delta: 2.5,
  trend: 'rising',
  top_reason: 'Autoencoder flagged anomaly',
  contributors: [
    { name: 'autoencoder_score', value: 0.22, weight: 0.1, contribution: 7.5 },
  ],
};

export const sampleTrainingConfig = {
  training_hours: 168,
  min_training_samples: 100,
  contamination: 0.05,
  n_estimators: 200,
  feature_bucket_minutes: 5,
  per_device_models: true,
  has_overrides: false,
};

export const sampleMlStatus = {
  devices: [
    {
      device_id: DEVICE_ID,
      hostname: 'test-device',
      model_type: 'isolation_forest',
      status: 'trained',
      training_samples: 120,
      threshold: -0.1,
      score_mean: -0.08,
      score_std: 0.05,
      trained_at: '2026-04-29T04:00:00',
    },
  ],
};

export const sampleGatewayWifiConfig = {
  ssid: 'IoT-Security',
  psk: 'change-me-please',
  country: 'PL',
  channel: 6,
  ap_interface: 'wlan0',
  upstream_interface: 'eth0',
  subnet_cidr: '192.168.50.0/24',
  gateway_ip: '192.168.50.1',
  dhcp_range_start: '192.168.50.100',
  dhcp_range_end: '192.168.50.200',
  enabled: true,
};

export const sampleGatewayStatus = {
  ap_interface_exists: true,
  upstream_interface_exists: true,
  ap_ip: '192.168.50.1',
  ip_forward: true,
  nat_rule_present: true,
  hostapd_running: true,
  dnsmasq_running: true,
  last_apply_ok: true,
  last_apply_message: 'Applied successfully',
};
