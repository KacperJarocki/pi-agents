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
| `k8s/argocd/` | Argo CD Applications for the operators and `gateway-agent`, plus the `pi-agents` ApplicationSet (one Application per environment) |
| `k8s/envs/` | Environment values: `base.yaml`, `<env>/values.yaml` (sizing), `<env>/images.yaml` (promotable image tags) |
| `k8s/values/` | Helm values of the operator Applications |
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

The whole deployment is driven by Argo CD from `k8s/argocd/`: Applications for the Strimzi, Flink and CloudNativePG operators (sync wave 0/1), `gateway-agent`, and an ApplicationSet that renders the `pi-agents` chart once per environment (wave 2). The homelab repo's Argo CD picks this directory up as a nested app-of-apps in the `pi-agents` AppProject. See `charts/pi-agents/README.md` for chart values and checkpoint profiles.

### Environments and promotion

| env | namespace | Application | values | deploys when |
|-----|-----------|-------------|--------|--------------|
| dev | `iot-security-dev` | `pi-agents-dev` | `k8s/envs/base.yaml` + `dev/values.yaml` + `dev/images.yaml` | every image build on `main` (`docker-build.yml` pins the new `sha-*` tags in `dev/images.yaml`) |
| prod | `iot-security` | `pi-agents` | `k8s/envs/base.yaml` + `prod/values.yaml` + `prod/images.yaml` | a promotion PR is merged |

Dev is a scaled-down copy on the same cluster (one broker, one TaskManager, one Postgres instance, Longhorn checkpoints, no backups, no capture sensor); drive it with `scripts/iot-device-emulator.sh` or `scripts/port-sweep.sh` against `pi-agents-kafka-bootstrap.iot-security-dev:9092`. Its dashboard is `https://iot-dashboard-dev.homelab.kacperjarocki.dev`.

To promote: run the **Promote to prod** workflow (Actions tab or `gh workflow run promote.yml`). It copies `dev/images.yaml` to `prod/images.yaml`, renders the prod profile and opens a PR with the tag diff; review, merge, and Argo CD rolls prod. Repository setting required: *Settings → Actions → General → Workflow permissions → Allow GitHub Actions to create and approve pull requests* (otherwise `gh pr create` fails with "GitHub Actions is not permitted to create or approve pull requests"). Set a `PROMOTE_TOKEN` repository secret (a PAT with `repo` scope) if you want the Validate workflow to run on those PRs; with the default token GitHub does not trigger workflows on bot-created PRs.

Anything that is not an image tag (sizing, feature switches, rules) is a normal PR against `k8s/envs/`; `base.yaml` changes hit both environments.

### Wi-Fi access point

`gateway-agent` runs with `hostNetwork` and privileges on the gateway node. With `ENABLE_APPLY=false` (the default) it validates and stores config but does not start hostapd. Its API (`/status`, `/validate`, `/apply`, `/rollback`, `/block`, `/blocked`) is reachable in-cluster at `http://gateway-agent.iot-security:7000`.

Default Wi-Fi config: SSID `IoT-Security`, PSK `change-me-please`.

Troubleshooting a missing SSID: make sure the `gateway-agent` Application in `k8s/argocd/` points at `k8s/overlays/gateway-prod` and `GET /status` reports `apply_enabled: true` and `hostapd.running: true`.

## Development

```bash
python -m unittest discover -v
python scripts/validate_event_contract.py
cargo test --manifest-path sensors/rust-sensor/Cargo.toml
helm lint charts/pi-agents -f charts/pi-agents/values-flink-s3.yaml
helm template pi-agents charts/pi-agents -n iot-security-dev -f k8s/envs/base.yaml -f k8s/envs/dev/values.yaml -f k8s/envs/dev/images.yaml
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
