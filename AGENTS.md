# pi-agents

## Project Overview

Agent-based IoT threat detection system. Gateway RPi acts as WiFi AP + traffic collector + ML anomaly detector running on K3s cluster.

## Cluster Architecture

- **3 Masters + 2 Workers** (worker-1 = AP gateway)
- **Prometheus + Grafana** already running
- **Ingress + LoadBalancer** already configured
- **Rook Ceph** for persistent storage

## Images

| Component | Image | Purpose |
|-----------|-------|---------|
| `gateway-api` | ghcr.io/kacperjarocki/gateway-api | REST API + WebSocket alerts |
| `collector` | ghcr.io/kacperjarocki/collector | Traffic capture via tcpdump/tshark |
| `ml-pipeline` | ghcr.io/kacperjarocki/ml-pipeline | ML training + inference |

## K8s Structure

```
k8s/
├── base/              # Namespace, PVC, NetworkPolicy, Ingress
└── gateway/          # All workload deployments
```

## K8s Workloads

| Component | Type | Schedule | Command |
|-----------|------|----------|---------|
| collector | Deployment | Always | collector app |
| gateway-api | Deployment | Always | uvicorn |
| ml-trainer | CronJob | 3:00 AM daily | train.py |
| ml-inference | Deployment | Always | inference.py loop |

## Building Images

Images are built automatically via GitHub Actions on push to `images/*`:

```
.github/workflows/docker-build.yml
```

Images pushed to: `ghcr.io/kacperjarocki/{image-name}`

Tags: `latest`, `sha-{git-sha}`

## Gateway Constraints (Critical)

- All pods MUST have CPU/memory limits (see README.md)
- hostapd runs native (not containerized) with higher priority
- ML training runs nightly at 3:00 AM
- collector uses hostNetwork mode for direct NIC access

## API Endpoints

- `GET /api/v1/devices` - Device list with risk scores
- `GET /api/v1/anomalies` - Recent anomalies
- `GET /api/v1/metrics/summary` - Dashboard metrics
- `WS /ws/alerts` - Real-time anomaly alerts

## ML Pipeline

- **Algorithm**: Isolation Forest (sklearn)
- **Features**: bytes, packets, unique destinations/ports, DNS queries, packet rate
- **Training**: CronJob at 3:00 AM
- **Inference**: Batch every 5 minutes
- **Minimum training samples**: 100 flows

## Dashboard

- Runs on separate host (not in K8s)
- Accesses API via ingress/LB
- Stack: FastAPI + HTMX + TailwindCSS
- Views: Devices, Timeline, Top Talkers, Anomalies

## Local Development

```bash
docker-compose up --build

# Services:
# - Dashboard: http://localhost:3000
# - API: http://localhost:8080
# - API Docs: http://localhost:8080/docs
```

## Troubleshooting

- collector needs `CAP_NET_ADMIN` + `CAP_NET_RAW` (securityContext)
- collector uses `hostNetwork: true` + `dnsPolicy: ClusterFirstWithHostNet`
- SQLite stored on Rook Ceph PVC at `/data/iot-security.db`
- Minimum 100 flows required for ML training
