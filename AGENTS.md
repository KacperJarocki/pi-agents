# pi-agents

## Project Overview

Agent-based IoT threat detection system. Gateway RPi acts as WiFi AP + traffic collector + ML anomaly detector running on K3s cluster.

## Cluster Architecture

- **3 Masters + 2 Workers** (worker-1 = AP gateway)
- **Traefik** ingress controller
- **cert-manager** with Let's Encrypt (Cloudflare DNS)
- **Longhorn** for persistent storage
- **Alloy** for metrics collection

## Infrastructure Stack

| Component | Technology | Config Location |
|-----------|------------|-----------------|
| Ingress | Traefik | homelab/infra/networking/traefik/ |
| TLS | cert-manager | homelab/infra/certmanager/ |
| Storage | Longhorn | homelab/infra/longhorn-system/ |
| GitOps | Flux CD | homelab/cluster/ |

## Images

| Component | Image | Registry |
|-----------|-------|----------|
| `gateway-api` | ghcr.io/kacperjarocki/gateway-api | REST API + WebSocket alerts |
| `collector` | ghcr.io/kacperjarocki/collector | Traffic capture via tcpdump/tshark |
| `gateway-agent` | ghcr.io/kacperjarocki/gateway-agent | WiFi AP + DHCP + NAT control |
| `ml-pipeline` | ghcr.io/kacperjarocki/ml-pipeline | ML training + inference |
| `dashboard` | ghcr.io/kacperjarocki/dashboard | Web UI |

## K8s Structure

```
k8s/
├── base/              # Namespace, PVC, NetworkPolicy
└── gateway/          # All workload deployments
```

## K8s Workloads

| Component | Type | Schedule | Command |
|-----------|------|----------|---------|
| collector | Deployment | Always | collector app |
| gateway-agent | Deployment | Always | gateway-agent API |
| gateway-api | Deployment | Always | uvicorn |
| ml-trainer | CronJob | 3:00 AM daily | train.py |
| ml-inference | Deployment | Always | inference.py loop |
| dashboard | Deployment | Always | FastAPI + HTMX |

## Building Images

Images are built automatically via GitHub Actions on push to `images/*`:

```yaml
# .github/workflows/docker-build.yml
```

Images pushed to: `ghcr.io/kacperjarocki/{image-name}`

Tags: `latest`, `sha-{git-sha}`

## Gateway Constraints (Critical)

- All pods MUST have CPU/memory limits (see README.md)
- WiFi AP is managed by the `gateway-agent` container (hostNetwork + privileged)
- ML training runs nightly at 3:00 AM
- collector uses hostNetwork mode for direct NIC access

## Labels Required

Label the gateway worker (worker-1):
```bash
kubectl label node <worker-1-name> node-role.kubernetes.io/gateway=true
```

## Ingress Configuration

Uses Traefik IngressRoute + cert-manager:

| Service | Host | TLS |
|---------|------|-----|
| gateway-api | `iot-api.homelab.kacperjarocki.dev` | Certificate |
| dashboard | `iot-dashboard.homelab.kacperjarocki.dev` | Certificate |

Issuer: `letsencrypt-http-prod` (Cloudflare DNS-01)

## API Endpoints

- `GET /api/v1/devices` - Device list with risk scores
- `GET /api/v1/anomalies` - Recent anomalies
- `GET /api/v1/metrics/summary` - Dashboard metrics
- `GET/PUT /api/v1/gateway/wifi/config` - WiFi config
- `POST /api/v1/gateway/wifi/validate` - Validate WiFi config
- `POST /api/v1/gateway/wifi/apply` - Apply WiFi config
- `POST /api/v1/gateway/wifi/rollback` - Rollback WiFi config
- `GET /api/v1/gateway/wifi/status` - Gateway status
- `WS /ws/alerts` - Real-time anomaly alerts

## ML Pipeline

- **Algorithm**: Isolation Forest (sklearn)
- **Features**: bytes, packets, unique destinations/ports, DNS queries, packet rate
- **Training**: CronJob at 3:00 AM
- **Inference**: Batch every 5 minutes
- **Minimum training samples**: 100 flows

## Dashboard

- Runs as K8s Deployment
- Accesses API via Traefik ingress
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
- SQLite stored on Longhorn PVC at `/data/iot-security.db`
- Minimum 100 flows required for ML training
