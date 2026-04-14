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
│  │  │ collector  │  │ ml-trainer │  │ ml-inference│   │   │
│  │  │ (Deploy)   │  │  (CronJob) │  │  (Deploy)   │   │   │
│  │  └────────────┘  └────────────┘  └────────────┘   │   │
│  │  ┌────────────┐  ┌────────────┐                    │   │
│  │  │gateway-api │  │  SQLite    │                    │   │
│  │  │  (Deploy)  │  │  (PVC)     │                    │   │
│  │  └────────────┘  └────────────┘                    │   │
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

## Images

| Component | Image | Registry |
|-----------|-------|----------|
| gateway-api | `gateway-api:latest` | ghcr.io/kacperjarocki |
| collector | `collector:latest` | ghcr.io/kacperjarocki |
| ml-pipeline | `ml-pipeline:latest` | ghcr.io/kacperjarocki |
| dashboard | `dashboard:latest` | ghcr.io/kacperjarocki |

## ML Pipeline

### ml-trainer (CronJob)
- **Schedule**: Daily at 3:00 AM
- **Task**: Trains Isolation Forest model on 7 days of data
- **Output**: Saves model to PVC

### ml-inference (Deployment)
- **Task**: Continuous anomaly detection
- **Interval**: Every 5 minutes
- **Output**: Writes anomalies to SQLite

## Deployment

### Prerequisites

- K3s cluster with 3 masters + 2 workers
- Rook Ceph for persistent storage
- Ingress controller (nginx)
- Prometheus/Grafana (optional)

### Deploy to K3s

```bash
kubectl apply -k k8s/base
kubectl apply -k k8s/gateway
```

### Local Development

```bash
docker-compose up --build
```

Services:
- Dashboard: http://localhost:3000
- API: http://localhost:8080
- API Docs: http://localhost:8080/docs

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/devices` | GET | List all devices |
| `/api/v1/devices/{id}` | GET | Get device details |
| `/api/v1/anomalies` | GET | List anomalies |
| `/api/v1/metrics/summary` | GET | System summary |
| `/api/v1/metrics/timeline` | GET | Traffic timeline |
| `/api/v1/metrics/top-talking` | GET | Top talkers |
| `/ws/alerts` | WS | Real-time alerts |

## Resource Limits

| Pod | CPU Request | CPU Limit | Memory |
|-----|------------|-----------|--------|
| collector | 100m | 300m | 256Mi |
| ml-trainer | 100m | 500m | 512Mi |
| ml-inference | 50m | 200m | 256Mi |
| gateway-api | 50m | 200m | 256Mi |
