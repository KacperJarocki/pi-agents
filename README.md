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
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐   │   │
│  │  │gateway-api │  │ dashboard  │  │  SQLite    │   │   │
│  │  │  (Deploy)  │  │  (Deploy) │  │ (Longhorn)│   │   │
│  │  └────────────┘  └────────────┘  └────────────┘   │   │
│  └──────────────────────────────────────────────────────┘   │
│                           │                                 │
│                    Traefik + cert-manager                   │
└───────────────────────────┼─────────────────────────────────┘
```

## Infrastructure Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| Ingress | Traefik | Routing traffic |
| TLS | cert-manager | Let's Encrypt certificates |
| Storage | Longhorn | Persistent volumes |
| GitOps | Flux CD | Deployment automation |
| Monitoring | Alloy | Metrics collection |

## URLs

| Service | URL |
|---------|-----|
| Gateway API | `https://iot-api.homelab.kacperjarocki.dev` |
| Dashboard | `https://iot-dashboard.homelab.kacperjarocki.dev` |

## Images

| Component | Image | Registry |
|-----------|-------|----------|
| gateway-api | `gateway-api:latest` | ghcr.io/kacperjarocki |
| collector | `collector:latest` | ghcr.io/kacperjarocki |
| ml-pipeline | `ml-pipeline:latest` | ghcr.io/kacperjarocki |
| dashboard | `dashboard:latest` | ghcr.io/kacperjarocki |

## K8s Structure

```
k8s/
├── base/              # Namespace, PVC, NetworkPolicy
└── gateway/          # All workload deployments
```

## K8s Workloads

| Component | Type | Schedule | Description |
|-----------|------|----------|-------------|
| collector | Deployment | Always | Traffic capture via tcpdump/tshark |
| gateway-api | Deployment | Always | REST API + WebSocket alerts |
| ml-trainer | CronJob | 3:00 AM | Isolation Forest training |
| ml-inference | Deployment | Always | Batch anomaly inference |
| dashboard | Deployment | Always | Web UI |

## Building Images

Images are built automatically via GitHub Actions on push to `images/*`:

```bash
# .github/workflows/docker-build.yml
```

Images pushed to: `ghcr.io/kacperjarocki/{image-name}`

Tags: `latest`, `sha-{git-sha}`

## Gateway Constraints (Critical)

- All pods MUST have CPU/memory limits (see below)
- WiFi AP is managed by the `gateway-agent` container (hostNetwork + privileged)
- ML training runs nightly at 3:00 AM with `nice +10`
- collector uses hostNetwork mode for direct NIC access

## Resource Limits

| Pod | CPU Request | CPU Limit | Memory |
|-----|------------|-----------|--------|
| collector | 100m | 300m | 256Mi |
| ml-trainer | 100m | 500m | 512Mi |
| ml-inference | 50m | 200m | 256Mi |
| gateway-api | 50m | 200m | 256Mi |
| dashboard | 50m | 100m | 128Mi |

## Deployment

### Prerequisites

- K3s cluster with 3 masters + 2 workers
- Longhorn for persistent storage
- Traefik ingress controller
- cert-manager with ClusterIssuer
- Label gateway worker: `kubectl label node <worker-1> node-role.kubernetes.io/gateway=true`

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

## ML Pipeline

- **Algorithm**: Isolation Forest (sklearn)
- **Features**: bytes, packets, unique destinations/ports, DNS queries, packet rate
- **Training**: CronJob at 3:00 AM
- **Inference**: Batch every 5 minutes
- **Minimum training samples**: 100 flows

## Troubleshooting

- collector needs `CAP_NET_ADMIN` + `CAP_NET_RAW` (securityContext)
- collector uses `hostNetwork: true` + `dnsPolicy: ClusterFirstWithHostNet`
- SQLite stored on Longhorn PVC at `/data/iot-security.db`
- Minimum 100 flows required for ML training
