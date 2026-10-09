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
- **Rust sensor** — `sensors/rust-sensor/` (currently only the bounded `FlowTable` core in `src/lib.rs`; capture, Protobuf, spool and Kafka publishing are separate tasks). `FlowTable` has a hard capacity; overflow surfaces as `UpsertResult::Evicted` and callers must expose it as a metric/health event.
- **Helm chart** — `charts/pi-agents/` owns the shared streaming config, Strimzi Kafka, and Flink templates. `streaming.enabled` defaults to `false`. Secrets are referenced, never stored in values. CI lints both Flink checkpoint profiles:
  ```bash
  helm lint charts/pi-agents -f charts/pi-agents/values-flink-longhorn.yaml
  helm lint charts/pi-agents -f charts/pi-agents/values-flink-s3.yaml
  helm template pi-agents charts/pi-agents -n iot-security -f charts/pi-agents/values-external.yaml
  ```
- **Flux** — `k8s/flux/` installs the Strimzi (and Flink) operators via HelmRelease, then the `pi-agents` chart. Manifests there must stay renderable with `kubectl kustomize k8s/flux`.
