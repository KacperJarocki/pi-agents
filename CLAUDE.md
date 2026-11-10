# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

`AGENTS.md` (imported above) covers layout, test commands, CI, gotchas and the git workflow. The sections below add detail on the streaming components.

## Plan and specs

`openspec/` holds spec-driven changes (`openspec/changes/<name>/{proposal,design,tasks}.md` plus `specs/`). The active change is `realtime-sliding-threat-detection`; check it before implementing streaming features and keep its tasks checklist current. Main specs under `openspec/specs/` are synced at archive time. `docs/STREAMING-ROADMAP.md` lists milestones (M0–M6) and task IDs (`EVT-*`, `PLAT-*`, `SENSOR-*`, `FLINK-*`, `MIG-*`).

The thesis-era batch pipeline is gone (tag `thesis-final`); do not reintroduce SQLite, polling inference or bucket-based features.

## Streaming components

- **Event contracts** — `schemas/events/v2/events.proto` is current; `v1` is frozen. Each version has `compatibility.json` pinning field numbers per message and `baseline.proto` as the previous released contract; changing field numbers/names breaks validation. Check without `protoc`:
  ```bash
  python scripts/validate_event_contract.py
  python -m unittest tests.test_event_contract_validator -v
  ```
- **Rust sensor** — `sensors/rust-sensor/`: frame parser, MAC attribution from dnsmasq leases, and the bounded micro-flow `FlowTable` (1 s idle/active flush) in a dependency-free core; libpcap capture only behind the `live` feature. Kafka publishing (feature `kafka`, librdkafka) goes through a bounded on-disk spool that assigns `capture_sequence`; the v2 Protobuf encoder is hand-written in `src/proto.rs` and tested against `compatibility.json`. A full `FlowTable` flushes its oldest flow early and returns `UpsertResult::Evicted`; callers must expose it as a metric/health event. Pcap fixtures come from `tests/fixtures/generate.py`.
- **Flink detection job** — `flink/detection-job/` (Java 17, Flink 1.20.5, Maven Wrapper). Protobuf classes are generated from `schemas/events/v2/events.proto` at build time; `baseline.proto` is excluded. Run `./mvnw verify` (MiniCluster tests). The image builds from the repository root: `docker build -f flink/detection-job/Dockerfile .`.
- **Helm chart** — `charts/pi-agents/` owns the shared streaming config, Strimzi Kafka, and Flink templates. `streaming.enabled` defaults to `false`. Secrets are referenced, never stored in values. CI lints both Flink checkpoint profiles:
  ```bash
  helm lint charts/pi-agents -f charts/pi-agents/values-flink-longhorn.yaml
  helm lint charts/pi-agents -f charts/pi-agents/values-flink-s3.yaml
  helm template pi-agents charts/pi-agents -n iot-security -f charts/pi-agents/values-external.yaml
  ```
- **Argo CD** — `k8s/argocd/` holds the operator Applications (cnpg-operator, barman-cloud, flink-operator, strimzi-operator at waves 0/1), `gateway-agent`, and the `pi-agents` ApplicationSet (wave 2) that renders the chart per environment: `pi-agents-dev` -> `iot-security-dev`, `pi-agents` -> `iot-security`. Values layer as `k8s/envs/base.yaml` < `<env>/values.yaml` < `<env>/images.yaml`. The homelab repo's Argo CD syncs this directory (nested app-of-apps, AppProject `pi-agents`).
- **Promotion** — image builds on `main` pin their `sha-*` tags into `k8s/envs/dev/images.yaml` (job `bump-dev` in `docker-build.yml`), so dev always runs the latest build. Prod changes only via the `Promote to prod` workflow (`promote.yml`), which copies dev's images.yaml to prod in a PR. Never edit `prod/images.yaml` by hand; sizing/feature changes go through `k8s/envs/` PRs. Keep both envs renderable with `helm template`.
