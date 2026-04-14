# pi-agents

## Project Overview

Agent-based IoT threat detection system. Gateway RPi acts as WiFi AP + traffic collector + ML anomaly detector running on K3s cluster.

## Cluster Architecture

- **3 Masters + 2 Workers** (worker-1 = AP gateway)
- **Prometheus + Grafana** already running
- **Ingress + LoadBalancer** already configured
- **Rook Ceph** for persistent storage

## Components

| Component | Image | Purpose |
|-----------|-------|---------|
| `collector` | ghcr.io/pi-agents/collector | Traffic capture via tcpdump/tshark |
| `ml-pipeline` | ghcr.io/pi-agents/ml-pipeline | Isolation Forest training + inference |
| `gateway-api` | ghcr.io/pi-agents/gateway-api | REST API + WebSocket alerts |
| `dashboard` | ghcr.io/pi-agents/dashboard | Web UI (separate host) |

## K8s Structure

```
k8s/
├── base/              # Namespace, PVC, NetworkPolicy, Ingress
└── gateway/          # All workload deployments
```

## Gateway Constraints (Critical)

- All pods MUST have CPU/memory limits (see README.md)
- hostapd runs native (not containerized) with higher priority
- ML training runs nightly at 3:00 AM with `nice +10`
- collector uses hostNetwork mode for direct NIC access

## Building Images

Use GitHub Actions: `.github/workflows/docker-build.yml`

```bash
# For local development
docker-compose up --build
```

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
# Start all services
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
