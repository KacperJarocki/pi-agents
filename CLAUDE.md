# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

`AGENTS.md` (imported above) is the primary reference for the legacy SQLite/batch system: test commands, Playwright, the `app` package-name collision, ML primary/shadow models, K8s deploy, and the git workflow. The sections below cover what it does not yet describe — the in-progress streaming rewrite.

## Two architectures side by side

The current pipeline (collector → SQLite → ml-trainer/ml-inference → gateway-api → dashboard) is the **legacy path** and stays authoritative. A streaming rewrite (Rust sensor → Kafka → Flink → PostgreSQL/ClickHouse/RustFS) is being built alongside it; see `docs/STREAMING-ROADMAP.md` for milestones (M0–M6) and task IDs (`EVT-*`, `PLAT-*`, `SENSOR-*`, `FLINK-*`). Do not remove or bypass legacy behavior until the roadmap's dual-run/cutover tasks prove parity. Streaming work must be ARM64-compatible and every workload needs resource requests and limits.

## Streaming components

- **Event contracts** — `schemas/events/v1/events.proto`. `compatibility.json` pins field numbers per message and `baseline.proto` is the previous released contract; changing field numbers/names breaks validation. Check without `protoc`:
  ```bash
  python scripts/validate_event_contract.py
  python -m unittest tests.test_event_contract_validator -v
  ```
- **Rust sensor** — `sensors/rust-sensor/` (currently only the bounded `FlowTable` core in `src/lib.rs`; capture, Protobuf, and Kafka publishing are deliberately separate). `FlowTable` has a hard capacity; overflow surfaces as `UpsertResult::Evicted` and callers must expose it as a metric/health event. CI (`rust-sensor.yml`) runs:
  ```bash
  cargo fmt --manifest-path sensors/rust-sensor/Cargo.toml -- --check
  cargo test --manifest-path sensors/rust-sensor/Cargo.toml
  cargo check --manifest-path sensors/rust-sensor/Cargo.toml --target aarch64-unknown-linux-gnu
  ```
- **Helm chart** — `charts/pi-agents/` owns the shared streaming config, Strimzi Kafka, and Flink templates. `streaming.enabled` defaults to `false`. Secrets are referenced, never stored in values. CI lints both Flink checkpoint profiles:
  ```bash
  helm lint charts/pi-agents -f charts/pi-agents/values-flink-longhorn.yaml
  helm lint charts/pi-agents -f charts/pi-agents/values-flink-s3.yaml
  helm template pi-agents charts/pi-agents -n iot-security -f charts/pi-agents/values-external.yaml
  ```
- **Flux** — `k8s/flux/` installs the Strimzi (and Flink) operators via HelmRelease, then the `pi-agents` chart. Manifests there must stay renderable with `kubectl kustomize k8s/flux`.

## Source-assertion tests

Many files in `tests/` (`test_*_sources.py`, `test_k8s_ingress_files.py`, `test_dashboard_templates_usage.py`) read source/manifest/doc files as text and assert on their contents rather than executing code. When renaming config keys, docs sections, script flags, or manifest fields, grep `tests/` for the old string — these tests will fail on otherwise-correct refactors.

## Specs

`openspec/` holds spec-driven change proposals (`openspec/changes/<name>/{proposal,design,tasks}.md` plus `specs/`). Check for an active change matching your task before implementing streaming features.
