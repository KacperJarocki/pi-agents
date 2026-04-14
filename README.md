# IoT Security Agent System

Agent-based IoT threat detection system running on K3s cluster with ML-powered anomaly detection.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      K3s Cluster                             │
│  Masters: 3x  |  Workers: 2x (worker-1 = AP Gateway)        │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Worker-AP (Gateway Node)                             │   │
│  │                                                      │   │
│  │  hostapd (native) ──── WiFi AP for IoT devices     │   │
│  │                                                      │   │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐   │   │
│  │  │ collector  │  │ ml-pipeline│  │ gateway-api │   │   │
│  │  │ (DaemonSet)│  │  (CronJob) │  │  (Deploy)   │   │   │
│  │  └────────────┘  └────────────┘  └────────────┘   │   │
│  │         │                │               │          │   │
│  │         └────────────────┼───────────────┘          │   │
│  │                          │                          │   │
│  │                   ┌──────┴──────┐                   │   │
│  │                   │ SQLite/PVC │                   │   │
│  │                   └────────────┘                   │   │
│  └──────────────────────────────────────────────────────┘   │
│                           │                                 │
│                      ingress/LB                             │
└───────────────────────────┼─────────────────────────────────┘
                            │
              ┌─────────────▼─────────────┐
              │      Dashboard App          │
              │  FastAPI + HTMX + Tailwind  │
              │  (separate host/node)       │
              └────────────────────────────┘
```

## Components

### collector
Captures network traffic from WiFi interface and extracts flow features.

- **Image**: `ghcr.io/pi-agents/collector:latest`
- **Interface**: hostNetwork mode for direct NIC access
- **Features**: src/dst IP, ports, protocol, bytes, DNS queries
- **Storage**: Writes to SQLite via shared PVC

### ml-pipeline
ML training and inference using Isolation Forest.

- **Training**: Runs nightly at 3:00 AM (CronJob)
- **Inference**: Batch inference every 5 minutes
- **Model**: Isolation Forest - CPU-friendly, no GPU required

### gateway-api
REST API for all system data.

- **Image**: `ghcr.io/pi-agents/gateway-api:latest`
- **Port**: 8080
- **Endpoints**: `/api/v1/devices`, `/api/v1/anomalies`, `/api/v1/metrics`
- **WebSocket**: `/ws/alerts`

### dashboard
Web UI for monitoring and visualization.

- **Image**: `ghcr.io/pi-agents/dashboard:latest`
- **Port**: 3000
- **Features**: Device list, Timeline, Top Talkers, Anomaly alerts
- **Stack**: FastAPI + HTMX + TailwindCSS

## Deployment

### Prerequisites

- K3s cluster with 3 masters + 2 workers
- Rook Ceph for persistent storage
- Ingress controller (nginx)
- Prometheus/Grafana (optional, for metrics)

### Deploy to K3s

```bash
# Apply base manifests
kubectl apply -k k8s/base

# Deploy gateway workloads
kubectl apply -k k8s/gateway

# Check status
kubectl get pods -n iot-security
```

### Local Development

```bash
# Build and run with Docker Compose
docker-compose up --build

# Access services:
# - Dashboard: http://localhost:3000
# - API: http://localhost:8080
# - API Docs: http://localhost:8080/docs
```

## API Reference

### Devices

```
GET  /api/v1/devices          - List all devices
GET  /api/v1/devices/{id}     - Get device details
POST /api/v1/devices          - Register new device
PATCH /api/v1/devices/{id}    - Update device
PUT  /api/v1/devices/{id}/risk-score - Update risk score
```

### Anomalies

```
GET  /api/v1/anomalies              - List anomalies
GET  /api/v1/anomalies/{id}        - Get anomaly details
POST /api/v1/anomalies              - Create anomaly
PATCH /api/v1/anomalies/{id}/resolve - Resolve anomaly
```

### Metrics

```
GET  /api/v1/metrics/summary        - System summary
GET  /api/v1/metrics/timeline       - Traffic timeline
GET  /api/v1/metrics/top-talking    - Top talkers
```

### WebSocket

```
WS /ws/alerts - Real-time anomaly alerts
```

## ML Pipeline

### Feature Engineering

Collected from traffic flows:

| Feature | Description |
|---------|-------------|
| total_bytes | Sum of sent + received bytes |
| packets | Total packet count |
| unique_destinations | Distinct destination IPs |
| unique_ports | Distinct destination ports |
| dns_queries | Number of DNS queries |
| avg_bytes_per_packet | Average bytes per packet |
| packet_rate | Packets per second |
| connection_duration_avg | Average connection duration |

### Training

- **Algorithm**: Isolation Forest
- **Contamination**: Auto-calculated (1-10% based on data size)
- **Samples required**: Minimum 100 for training
- **Retraining**: Daily at 3:00 AM

### Anomaly Detection

Anomalies are scored and categorized:

| Severity | Score Range | Action |
|----------|-------------|--------|
| critical | < -1.0 | Immediate alert |
| warning | -0.5 to -1.0 | Log and monitor |

## Resource Limits

All pods have CPU/memory limits to ensure gateway stability:

| Pod | CPU Request | CPU Limit | Memory |
|-----|-------------|-----------|--------|
| collector | 100m | 300m | 256Mi |
| ml-pipeline | 100m | 500m | 512Mi |
| gateway-api | 50m | 200m | 256Mi |

## Monitoring

### Prometheus Metrics

- `gateway_api_requests_total` - Request counter
- `gateway_api_request_duration_seconds` - Request latency
- `gateway_api_active_devices` - Active device gauge
- `gateway_api_active_anomalies` - Unresolved anomaly gauge

### Grafana Dashboard

Import `grafana/dashboard.json` for pre-built visualization.

## Troubleshooting

### Collector not capturing traffic

1. Check interface: `kubectl exec -n iot-security deploy/collector -- ip link show`
2. Verify CAP_NET_RAW: `kubectl describe pod -n iot-security -l app=collector | grep -A 10 "Security Context"`
3. Check logs: `kubectl logs -n iot-security -l app=collector`

### ML training fails

1. Check minimum data: `kubectl exec -n iot-security deploy/gateway-api -- sqlite3 /data/iot-security.db "SELECT COUNT(*) FROM traffic_flows"`
2. Minimum required: 100 flows

### Dashboard not connecting to API

1. Check ingress: `kubectl get ingress -n iot-security`
2. Verify DNS resolution: `nslookup gateway-api.local`
3. Check API health: `curl http://gateway-api.iot-security:8080/health`

## License

MIT
