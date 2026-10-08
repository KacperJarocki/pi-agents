# IoT Security Agent System

Real-time IoT threat detection on a K3s cluster of Raspberry Pis. A gateway Pi runs the Wi-Fi access point, captures device traffic and publishes it to Kafka; Flink keeps per-device 60-second and 900-second sliding views and pushes incidents to the browser as soon as evidence crosses a rule threshold.

The batch SQLite/ML pipeline from the master's thesis (collector, ml-pipeline, gateway-api, dashboard) has been removed. Its last state is tagged `thesis-final`.

## Status

The streaming system is under construction. The active plan is the OpenSpec change `openspec/changes/realtime-sliding-threat-detection/` (`proposal.md`, `design.md`, `tasks.md`); `docs/STREAMING-ROADMAP.md` holds the wider backlog. Until the sensor, Flink job and incident API are deployed there is no detection running.

## Architecture

```
gateway Pi                     cluster (non-gateway nodes)
┌──────────────────┐          ┌───────────────────────────────────────────────┐
│ gateway-agent    │          │ Kafka (Strimzi, KRaft)                        │
│  hostapd/dnsmasq │          │   traffic.flows.v2 ──► Flink port-scan job     │
│ rust-sensor ─────┼─────────►│                         │                     │
│  micro-flows,    │          │   traffic.detections.v2 ◄┘                     │
│  disk spool      │          │        │                                       │
└──────────────────┘          │        ▼                                       │
                              │ incident projector ──► PostgreSQL (CNPG)       │
                              │                          │ LISTEN/NOTIFY       │
                              │ incident-api (REST + WS + /live page) ◄┘       │
                              │ RustFS (Flink checkpoints, PG backups)         │
                              └───────────────────────────────────────────────┘
```

Planned components are described in `design.md` of the active change. What exists today:

| Path | Purpose |
|------|---------|
| `images/gateway-agent/` | Wi-Fi AP controller (hostapd/dnsmasq), FastAPI on port 7000 |
| `sensors/rust-sensor/` | Rust capture sensor (bounded `FlowTable` core so far) |
| `schemas/events/v2/` | Protobuf event contract (`v1` is frozen) |
| `charts/pi-agents/` | Helm chart: streaming config, Strimzi Kafka, Flink |
| `k8s/flux/` | Flux sources and HelmReleases for operators and the chart |
| `k8s/base`, `k8s/gateway`, `k8s/overlays/gateway-prod` | Namespace, streaming ConfigMap and `gateway-agent` |
| `scripts/` | Event contract validator, capture benchmark, traffic generators |

## Hardware

5× Raspberry Pi 5 (8 GB RAM, NVMe). One node is labelled as the gateway and runs only the AP and the sensor:

```bash
kubectl label node <gateway-node> node-role.kubernetes.io/gateway=true
```

All workloads are ARM64 and must declare resource requests and limits.

## Deployment

```bash
kubectl apply -k k8s/gateway                 # namespace, streaming config, gateway-agent (safe mode)
kubectl apply -k k8s/overlays/gateway-prod   # same, with ENABLE_APPLY=true so the SSID comes up
```

The streaming platform is deployed by Flux from `k8s/flux` (Strimzi and Flink operators, then the `pi-agents` chart). See `charts/pi-agents/README.md` for values and checkpoint profiles.

### Wi-Fi access point

`gateway-agent` runs with `hostNetwork` and privileges on the gateway node. With `ENABLE_APPLY=false` (the default) it validates and stores config but does not start hostapd. Its API (`/status`, `/validate`, `/apply`, `/rollback`, `/block`, `/blocked`) is reachable in-cluster at `http://gateway-agent.iot-security:7000`.

Default Wi-Fi config: SSID `IoT-Security`, PSK `change-me-please`.

Troubleshooting a missing SSID: make sure `k8s/overlays/gateway-prod` is applied and `GET /status` reports `apply_enabled: true` and `hostapd.running: true`.

## Development

```bash
python -m unittest discover -v
python scripts/validate_event_contract.py
cargo test --manifest-path sensors/rust-sensor/Cargo.toml
helm lint charts/pi-agents -f charts/pi-agents/values-flink-s3.yaml
kubectl kustomize k8s/flux
```

`docker-compose --profile gateway up --build` runs `gateway-agent` locally (Linux only; host network and privileged).

## Traffic research scripts

Generate controlled port-sweep traffic from a device on the IoT Wi-Fi:

```bash
./scripts/port-sweep.sh --target 192.168.50.1 --profile positive
```

Profiles `negative`, `borderline`, `positive`, `slow` and `aggressive` are the fixtures for validating port-scan rule thresholds. Each run writes metadata to `artifacts/port-sweep/<run-id>/`; `--seed 42` makes randomized runs reproducible.

Run the overnight balanced research protocol (35 phases, about 6h35m with a 5-minute gap) in the background:

```bash
python3 research.py
tail -f artifacts/research-runs/<run-id>/research.log
```

It discovers reachable hosts in the local `/24`; use `--no-discover --target 192.168.50.1` when client isolation blocks discovery. Results and phase markers land in `artifacts/research-runs/<run-id>/`.

Generate benign IoT-like baseline traffic:

```bash
./scripts/iot-device-emulator.sh --profile sensor --duration 30m
```

## Images

Built by `.github/workflows/docker-build.yml` and pushed to `ghcr.io/kacperjarocki/<name>` with tags `latest` and `sha-<8 chars>` (multi-arch on `main`, amd64-only build on PRs). Currently only `gateway-agent`.
