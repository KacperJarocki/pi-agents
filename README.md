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
│  │  gateway-agent (pod) ─ WiFi AP + DHCP + NAT        │   │
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

Default WiFi config:
- SSID: `IoT-Security`
- PSK: `change-me-please`

## Images

| Component | Image | Registry |
|-----------|-------|----------|
| gateway-api | `gateway-api:latest` | ghcr.io/kacperjarocki |
| collector | `collector:latest` | ghcr.io/kacperjarocki |
| gateway-agent | `gateway-agent:latest` | ghcr.io/kacperjarocki |
| ml-pipeline | `ml-pipeline:latest` | ghcr.io/kacperjarocki |
| dashboard | `dashboard:latest` | ghcr.io/kacperjarocki |

## K8s Structure

```
k8s/
├── base/              # Namespace and PVC
├── gateway/           # All workload deployments
└── overlays/          # Environment-specific overrides
```

## K8s Workloads

| Component | Type | Schedule | Description |
|-----------|------|----------|-------------|
| collector | Deployment | Always | Traffic capture via tcpdump/tshark |
| gateway-agent | Deployment | Always | WiFi AP + DHCP + NAT control |
| gateway-api | Deployment | Always | REST API + WebSocket alerts |
| ml-trainer | CronJob | Every 30 min | Isolation Forest training |
| ml-inference | Deployment | Always | Batch anomaly inference |
| dashboard | Deployment | Always | Web UI |

## Component Docs

- `images/gateway-agent/README.md`
- `images/collector/README.md`
- `images/gateway-api/README.md`
- `images/dashboard/README.md`
- `images/ml-pipeline/README.md`
- `k8s/README.md`
- `docs/MVP-VERIFICATION.md`

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
- ML training runs every 30 minutes for MVP
- collector uses hostNetwork mode for direct NIC access

## Resource Limits

| Pod | CPU Request | CPU Limit | Memory |
|-----|------------|-----------|--------|
| collector | 100m | 300m | 256Mi |
| ml-trainer | 100m | 500m | 512Mi |
| ml-inference | 100m | 300m | 512Mi |
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

Ingress is exposed using standard Kubernetes `Ingress` resources with `ingressClassName: traefik`.

### Enable WiFi AP Control (Required For SSID)

By default `gateway-agent` is deployed with `ENABLE_APPLY=false` (safe mode). This means the SSID will not appear until you enable apply.

Use the production overlay to enable AP control:

```bash
kubectl apply -k k8s/overlays/gateway-prod
```

### Local Development

```bash
docker-compose up --build
```

Gateway-only services (privileged, may not work on non-Linux hosts):

```bash
docker-compose --profile gateway up --build
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
| `/api/v1/devices/{id}/traffic` | GET | 24h traffic profile for the device |
| `/api/v1/devices/{id}/destinations` | GET | Top destinations, ports and DNS queries |
| `/api/v1/devices/{id}/inference-history` | GET | 7-day inference trail |
| `/api/v1/devices/{id}/behavior-alerts` | GET | Recent heuristic behavior alerts |
| `/api/v1/devices/{id}/risk-contributors` | GET | Active ML and heuristic contributors |
| `/api/v1/devices/{id}/behavior-baseline` | GET | Per-device median and p95 baseline |
| `/api/v1/devices/{id}/protocol-signals` | GET | DNS failure and ICMP signal summary |
| `/api/v1/anomalies` | GET | List anomalies |
| `/api/v1/metrics/summary` | GET | System summary |
| `/api/v1/metrics/timeline` | GET | Traffic timeline |
| `/api/v1/metrics/top-talking` | GET | Top talkers |
| `/api/v1/metrics/ml-status` | GET | ML model readiness status |
| `/api/v1/gateway/wifi/config` | GET/PUT | Read/update WiFi config |
| `/api/v1/gateway/wifi/validate` | POST | Validate WiFi config |
| `/api/v1/gateway/wifi/apply` | POST | Apply WiFi config |
| `/api/v1/gateway/wifi/rollback` | POST | Rollback to last-known-good |
| `/api/v1/gateway/wifi/status` | GET | Gateway agent status |
| `/ws/alerts` | WS | Real-time alerts |

## ML Pipeline

- **Algorithm**: Isolation Forest (sklearn)
- **Features**: bucketed per-device samples (bytes, packets, unique destinations/ports, DNS queries, packet rate)
- **Training**: CronJob every 30 minutes for MVP
- **Inference**: Batch every 60 seconds on 2-minute buckets
- **MVP mode**: per-device models with adaptive device baseline support
- **Minimum training samples**: 20 per-device buckets

## Detection Layers

- ML scoring is combined with heuristic behavior alerts to raise `risk_score` faster on suspicious bursts.
- Current heuristic alerts include `destination_novelty`, `dns_burst`, `port_churn`, `traffic_pattern_drift`, `beaconing_suspected`, `dns_failure_spike`, and `icmp_sweep_suspected`.
- The collector enriches flows with DNS response codes and ICMP metadata so protocol-level signals can be shown in the API and dashboard.
- Risk Engine v2 now breaks the final score into `ml_risk`, `behavior_risk`, `protocol_risk`, and `correlation_bonus` so the device console can explain why risk changed.
- Protocol heuristics now also include `dns_nxdomain_burst` and `icmp_echo_fanout` for low-cost DGA-like and sweep-like detection.

## Device Console

- Dashboard device detail pages expose a SOC-style view with traffic profile, inference trail, top destinations, top ports, top DNS queries, behavior alerts, risk contributors, behavior baseline, and protocol signals.
- The device console also shows a `Risk Breakdown` panel with previous risk, risk delta, contributor status, and the current top reason driving the score.

## Device Presence

- Connected devices are sourced from `dnsmasq` DHCP leases exposed by `gateway-agent`
- Recent traffic is used as a fallback signal when a lease is missing
- Dashboard devices may appear even before collector has built a persistent device record

## Troubleshooting

- SSID not visible:
  - Ensure `k8s/overlays/gateway-prod` is applied (sets `ENABLE_APPLY=true`)
  - Check `GET /api/v1/gateway/wifi/status` and look for `apply_enabled: true` and `hostapd.running: true`

- collector needs `CAP_NET_ADMIN` + `CAP_NET_RAW` (securityContext)
- collector uses `hostNetwork: true` + `dnsPolicy: ClusterFirstWithHostNet`
- collector metrics endpoint is disabled for MVP to avoid conflicts with host-level exporters like `node_exporter`
- SQLite stored on Longhorn PVC at `/data/iot-security.db`
- Minimum training samples for MVP are 20 per-device buckets
