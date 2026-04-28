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
├── base/              # Namespace and PVC
├── gateway/           # All workload deployments
└── overlays/          # Environment-specific overrides
```

## K8s Workloads

| Component | Type | Schedule | Command |
|-----------|------|----------|---------|
| collector | Deployment | Always | collector app |
| gateway-agent | Deployment | Always | gateway-agent API |
| gateway-api | Deployment | Always | uvicorn |
| ml-trainer | CronJob | Every 30 min | train.py |
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
- ML training runs every 30 minutes for MVP
- collector uses hostNetwork mode for direct NIC access

## Labels Required

Label the gateway worker (worker-1):
```bash
kubectl label node <worker-1-name> node-role.kubernetes.io/gateway=true
```

## Ingress Configuration

Uses standard Kubernetes Ingress + cert-manager:

Note: manifests now use standard Kubernetes `Ingress` with `ingressClassName: traefik` and cert-manager TLS secrets.

| Service | Host | TLS |
|---------|------|-----|
| gateway-api | `iot-api.homelab.kacperjarocki.dev` | Certificate |
| dashboard | `iot-dashboard.homelab.kacperjarocki.dev` | Certificate |

Issuer: `letsencrypt-http-prod` (Cloudflare DNS-01)

## API Endpoints

### Core
- `GET /health` - Health check
- `GET /metrics` - Prometheus metrics
- `GET /api/v1/devices` - Device list with risk scores
- `POST /api/v1/devices` - Create device
- `GET /api/v1/devices/{id}` - Single device detail
- `PATCH /api/v1/devices/{id}` - Update device
- `GET /api/v1/anomalies` - Recent anomalies (unresolved)
- `POST /api/v1/anomalies` - Create anomaly
- `GET /api/v1/anomalies/{id}` - Single anomaly
- `PATCH /api/v1/anomalies/{id}/resolve` - Resolve anomaly
- `GET /api/v1/alerts` - Unified alert feed (anomalies + behavior_alerts)
- `POST /api/v1/alerts/broadcast` - Broadcast alert via WS
- `WS /ws/alerts` - Real-time anomaly alerts (echo-only; dashboard polls via GET /api/v1/alerts)

### Device Sub-resources
- `GET /api/v1/devices/{id}/traffic` - Recent traffic flows
- `GET /api/v1/devices/{id}/destinations` - Destination IPs
- `GET /api/v1/devices/{id}/anomalies` - Device anomalies
- `GET /api/v1/devices/{id}/inference-history` - ML inference history
- `GET /api/v1/devices/{id}/behavior-alerts` - Behavior alerts
- `GET /api/v1/devices/{id}/risk-contributors` - Risk score breakdown
- `GET /api/v1/devices/{id}/behavior-baseline` - Behavior baseline stats
- `GET /api/v1/devices/{id}/protocol-signals` - Protocol-level signals
- `GET /api/v1/devices/{id}/model-config` - Per-device ML config
- `PUT /api/v1/devices/{id}/model-config` - Update per-device ML config
- `GET /api/v1/devices/{id}/model-scores` - Historical model scores
- `POST /api/v1/devices/{id}/block` - Block device
- `DELETE /api/v1/devices/{id}/block` - Unblock device
- `PUT /api/v1/devices/{id}/risk-score` - Manual risk override

### Metrics
- `GET /api/v1/metrics/summary` - Dashboard metrics
- `GET /api/v1/metrics/ml-status` - ML model readiness status
- `GET /api/v1/metrics/timeline` - Traffic timeline
- `GET /api/v1/metrics/top-talking` - Top talkers

### WiFi Gateway
- `GET/PUT /api/v1/gateway/wifi/config` - WiFi config
- `POST /api/v1/gateway/wifi/validate` - Validate WiFi config
- `POST /api/v1/gateway/wifi/apply` - Apply WiFi config
- `POST /api/v1/gateway/wifi/rollback` - Rollback WiFi config
- `GET /api/v1/gateway/wifi/status` - Gateway status
- `GET /api/v1/gateway/wifi/blocked` - Blocked devices list

### ML Pipeline Management
- `GET /api/v1/ml/config` - Global training config
- `PUT /api/v1/ml/config` - Update global training config
- `GET /api/v1/ml/devices/{id}/training-config` - Per-device training config
- `PUT /api/v1/ml/devices/{id}/training-config` - Update per-device training config
- `DELETE /api/v1/ml/devices/{id}/training-config` - Reset to global defaults
- `GET /api/v1/ml/devices/{id}/training-data` - Feature buckets for device
- `GET /api/v1/ml/devices/{id}/raw-flows` - Raw traffic flows for device
- `POST /api/v1/ml/devices/{id}/train` - Trigger on-demand training (K8s Job); optional query params: `cpu_request`, `cpu_limit`, `mem_request`, `mem_limit` override Job resource requests/limits (e.g. `cpu_request=200m&mem_limit=1Gi`); defaults: `100m`/`500m` CPU, `256Mi`/`512Mi` memory
- `GET /api/v1/ml/devices/{id}/train/status` - Training job status

## ML Pipeline

- **Algorithms**: Isolation Forest, LOF, OCSVM, Autoencoder (sklearn/keras)
- **Features**: bucketed per-device samples (bytes, packets, unique destinations/ports, DNS queries, packet rate, avg bytes/packet, connection duration avg)
- **Training**: CronJob every 30 minutes for MVP; on-demand via K8s Job
- **Inference**: Batch every 5 minutes (configurable via INFERENCE_INTERVAL)
- **MVP mode**: per-device models with 5-minute buckets (configurable via FEATURE_BUCKET_MINUTES)
- **Minimum training samples**: 20 per-device buckets (configurable)
- **Adaptive threshold**: contamination = max(0.03, min(0.1, 5.0 / samples))
- **Score normalization**: Raw decision scores normalized to 0–1 via training score statistics
- **Global training fallback**: Trains single model on all devices if per-device sample count < minimum

## Device Presence

- Connected devices are derived from `dnsmasq` DHCP leases exposed by `gateway-agent`
- Recent traffic is used as a fallback signal when a lease is missing

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
- collector metrics endpoint is disabled for MVP to avoid conflicts with host-level exporters like `node_exporter`
- SQLite stored on Longhorn PVC at `/data/iot-security.db`
- Minimum training samples for MVP are 20 per-device buckets

## Database Schema

Single SQLite file at `/data/iot-security.db` shared by collector, ml-pipeline, and gateway-api.
WAL mode enabled, `busy_timeout=5000ms` on every connection.

### Tables

#### `devices`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Auto-increment |
| mac_address | TEXT UNIQUE | Fallback `ip:<ip>` when MAC unavailable |
| ip_address | TEXT | Current IP from DHCP lease |
| hostname | TEXT | From dnsmasq lease or None |
| device_type | TEXT | Currently unused |
| first_seen | TIMESTAMP | Set on insert |
| last_seen | TIMESTAMP | Updated on every flow flush |
| is_active | INTEGER | 1 = active |
| risk_score | REAL | Final composite risk 0–100 |
| last_inference_score | REAL | Raw IsolationForest score |
| last_inference_at | TIMESTAMP | Last ml-inference write |
| extra_data | TEXT (JSON) | Aggregated stats from collector |

#### `traffic_flows`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| device_id | INTEGER FK | → devices.id |
| timestamp | TIMESTAMP | Packet capture time |
| src_ip / dst_ip | TEXT | |
| src_port / dst_port | INTEGER | 0 for ICMP |
| protocol | TEXT | TCP / UDP / ICMP / … |
| bytes_sent | INTEGER | frame.len from tshark |
| bytes_received | INTEGER | Currently 0 (collector writes bytes_sent only) |
| packets | INTEGER | Default 1 per flow row |
| duration_ms | INTEGER | Default 0 |
| dns_query | TEXT | dns.qry.name if present |
| flags | TEXT (JSON) | `{dns_rcode, icmp_type, icmp_code}` |

Retention: batch-deleted by ml-inference every 5 min, keep 7 days.

#### `anomalies`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| device_id | INTEGER FK | |
| timestamp | TIMESTAMP | |
| anomaly_type | TEXT | `isolation_forest` |
| severity | TEXT | `warning` / `critical` |
| score | REAL | IsolationForest decision score |
| description | TEXT | Human-readable |
| features | TEXT (JSON) | Feature snapshot at detection time |
| resolved | INTEGER | 0 = open; auto-resolved after 48 h |
| resolved_at | TIMESTAMP | |

#### `device_inference_history`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| device_id | INTEGER FK | |
| timestamp | TIMESTAMP | Write time |
| bucket_start | TIMESTAMP | 5-min feature bucket |
| anomaly_score | REAL | |
| risk_score | REAL | Composite 0–100 |
| is_anomaly | INTEGER | 1 if threshold breached |
| severity | TEXT | |
| features | TEXT (JSON) | Full feature + risk breakdown |

Retention: 7 days.

#### `device_behavior_alerts`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| device_id | INTEGER FK | |
| timestamp | TIMESTAMP | |
| bucket_start | TIMESTAMP | 5-min feature bucket (dedup key) |
| alert_type | TEXT | See alert types below |
| severity | TEXT | `warning` / `critical` |
| score | REAL | Heuristic score 0–100 |
| title | TEXT | |
| description | TEXT | |
| evidence | TEXT (JSON) | Raw numbers backing the alert |
| resolved | INTEGER | 0 = open |

Alert types: `destination_novelty`, `dns_burst`, `port_churn`, `traffic_pattern_drift`, `beaconing_suspected`, `dns_failure_spike`, `dns_nxdomain_burst`, `icmp_sweep_suspected`, `icmp_echo_fanout`

Retention: 14 days.

#### `model_metadata`
Stores training run records (model type, version, sample count, accuracy). Written by ml-trainer CronJob.

**Note**: Legacy production DBs have a `version TEXT NOT NULL` column not present in newer CREATE TABLE statements. The code inserts `version = "1.0"` for compatibility.

| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| device_id | INTEGER | NULL for global models |
| model_type | TEXT | isolation_forest / lof / ocsvm / autoencoder |
| version | TEXT | Legacy NOT NULL column, set to "1.0" |
| trained_at | TIMESTAMP | Training completion time |
| training_samples | INTEGER | Number of feature buckets used |
| features_count | INTEGER | Number of features |
| contamination | REAL | Adaptive contamination rate |
| threshold | REAL | Decision function threshold |
| score_mean / score_std | REAL | Training score statistics for normalization |
| accuracy | REAL | |
| features_used | TEXT | |
| parameters | TEXT (JSON) | Full model parameters |
| is_active | INTEGER | 0/1 |
| training_hours | INTEGER | Hours of data used |

#### `global_training_config`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | Always 1 (singleton) |
| model_type | TEXT | Default algorithm |
| training_hours | INTEGER | Hours of training data |
| min_samples | INTEGER | Minimum buckets required |
| bucket_minutes | INTEGER | Feature bucket width |
| contamination_min / max | REAL | Adaptive contamination range |
| updated_at | TIMESTAMP | |

#### `device_training_config`
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER PK | |
| device_id | INTEGER UNIQUE | → devices.id |
| model_type | TEXT | Override algorithm |
| training_hours | INTEGER | Override training window |
| min_samples | INTEGER | Override minimum buckets |
| bucket_minutes | INTEGER | Override bucket width |
| contamination_min / max | REAL | Override contamination range |
| enabled | INTEGER | 1 = use overrides |
| updated_at | TIMESTAMP | |

---

## Data Flow

```
  WiFi clients
       │ 802.11
       ▼
  [gateway-agent]  ── hostapd + dnsmasq ──► /gateway-state/dnsmasq.leases
       │
       │ NAT / routing
       ▼
  [collector]  (hostNetwork, wlan0)
    tcpdump -c 150 → /tmp/capture_<pid>_<n>.pcap
    tshark → parse fields (ip, port, proto, dns, icmp, frame.len)
    resolve device via dnsmasq lease (10s TTL cache)
    batch INSERT → traffic_flows
    UPDATE devices.extra_data (aggregated stats)
       │
       │ SQLite WAL  /data/iot-security.db
       ▼
  [ml-trainer]  (CronJob, every 30 min)
    SELECT traffic_flows WHERE timestamp >= now - 24h
    FeatureExtractor: 5-min buckets per device
      (total_bytes, packets, unique_destinations, unique_ports,
       dns_queries, avg_bytes_per_packet, packet_rate,
       connection_duration_avg)
    IsolationForest fit per device (min 20 buckets)
    joblib.dump → /data/models/isolation_forest_model_device_<id>.joblib
       │
       ▼
  [ml-inference]  (Deployment, loop every 5 min)
    get_all_recent_flows(hours=max(24, 168))
    FeatureExtractor → latest bucket per device
    AnomalyDetector.score → decision_function (cached model, mtime-keyed)
    _build_behavior_alerts (9 heuristics on latest vs history buckets)
    _risk_with_contributors → composite risk 0–100
    batch_save_inference_cycle (single DB connection):
      UPDATE devices.risk_score
      INSERT device_inference_history
      INSERT device_behavior_alerts (dedup by device+type+bucket)
      INSERT anomalies (if IsolationForest threshold breached)
    run_retention_cleanup (batch DELETE LIMIT 5000, commit per batch)
       │
       ▼
  [gateway-api]  (FastAPI, uvicorn)
    SQLAlchemy async + aiosqlite, WAL mode
    TTLCache (asyncio.Lock, single-flight) for hot endpoints
    GET /api/v1/devices        → devices + risk_score + behavior_alerts
    GET /api/v1/anomalies      → anomalies (unresolved)
    GET /api/v1/metrics/*      → summary + ml-status
    WS  /ws/alerts             → polls anomalies every 30s, pushes delta
       │
       ▼
  [dashboard]  (FastAPI + HTMX + TailwindCSS)
    Fetches from gateway-api via Traefik ingress
    Views: Devices, Timeline, Top Talkers, Anomalies
    WS client with exponential backoff (1s → 30s), toast alerts
```

### Risk Score Composition (0–100)

```
ml_risk          (0–35)   _risk_from_score(IsolationForest decision score)
+ behavior_risk  (0–35)   heuristic alerts, capped per alert type
+ protocol_risk  (0–20)   DNS/ICMP protocol alerts
+ correlation_bonus (0–15) ML + heuristics firing together
= final_risk     (0–100)  stored in devices.risk_score
```
