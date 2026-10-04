# Streaming Platform Roadmap

This document is the execution backlog for the production real-time analytics
rewrite. The current SQLite/batch pipeline remains the legacy path until the
dual-run and cutover tasks are complete.

## Definition of Ready

A task is ready when its scope, dependencies, interfaces, resource budget,
acceptance criteria, test plan, rollout plan, and rollback plan are documented.
Any required architecture decision must be accepted before implementation.

## Definition of Done

A task is done when its implementation is reviewed, tested, observable,
ARM64-compatible, configured with resource requests and limits, documented, and
verified in the target Kubernetes environment. Failure modes and rollback must
be covered. No task may silently remove the legacy path before dual-run proves
parity.

## Milestones

| ID | Outcome |
| --- | --- |
| M0 | Capacity measurements, ADRs, event contracts, and SLOs |
| M1 | Kafka, Flink, RustFS, PostgreSQL, and ClickHouse platform |
| M2 | Rust sensor with Kafka publishing and PCAP ring |
| M3 | Stateful Flink features and sub-two-second fast detections |
| M4 | Operations UI with live alerts and incident workflow |
| M5 | Analytics Workbench and PCAP analysis jobs |
| M6 | Event-driven ML, dual-run, and SQLite retirement |

## Execution Backlog

### M0: Foundations

- `ARCH-001` Measure packet rate, bandwidth, NVMe, NAS throughput, and PCAP growth.
- `ARCH-002` Record ADRs for Kafka, Flink, Rust sensor, storage, and UI choices.
- `ARCH-003` Define latency, recovery, retention, and data-loss SLOs.
- `ARCH-004` Define node placement and CPU/RAM/storage budgets.
- `EVT-001` Define the versioned event envelope and time semantics.
- `EVT-002` Define Protobuf schemas for flows, features, detections, alerts, and captures.
- `EVT-003` Define topic keys, partitions, retention, compaction, and DLQ policy.
- `EVT-004` Add schema compatibility checks to CI.

### M1: Platform

- `PLAT-001` Add an ARM64-compatible umbrella Helm chart with internal/external profiles.
- `PLAT-002` Add internal/external PostgreSQL configuration and migrations.
- `PLAT-003` Add internal/external ClickHouse configuration and schemas.
- `PLAT-004` Deploy three Kafka KRaft brokers on separate nodes and local NVMe.
- `PLAT-005` Deploy Flink through the Kubernetes Operator with RustFS checkpoints.
- `PLAT-006` Configure RustFS S3 buckets, lifecycle policies, and credentials.
- `PLAT-007` Build and smoke-test all images for `linux/arm64`.

### M2: Ingest

- `SENSOR-001` Benchmark libpcap/AF_PACKET capture on the target Raspberry Pi.
- `SENSOR-002` Implement a bounded Rust packet parser with PCAP fixtures and fuzzing.
- `SENSOR-003` Implement an in-memory flow table with bounded state.
- `SENSOR-004` Implement an idempotent Protobuf Kafka producer.
- `SENSOR-005` Implement a durable local disk spool for Kafka outages.
- `SENSOR-006` Implement the local PCAP ring and asynchronous RustFS upload.
- `SENSOR-007` Run the new sensor beside the legacy collector and compare output.

### M3: Analytics

- `FLINK-001` Validate, deduplicate, and route malformed events to a DLQ.
- `FLINK-002` Add event-time watermarks and late-event side outputs.
- `FLINK-003` Add non-blocking enrichment and threat-intelligence broadcast state.
- `FLINK-004` Reproduce the existing feature set with golden datasets.
- `FLINK-005` Add one-second and ten-second fast-path features.
- `FLINK-006` Add CEP/rule detections for scans, bursts, beacons, and IOC traffic.
- `FLINK-007` Publish features, detections, and pipeline health topics.
- `FLINK-008` Test checkpoint recovery, backpressure, and broker loss.

### M4-M6: Serving, UI, ML, and migration

- `API-001` Split control-plane and analytics APIs.
- `API-002` Add bounded ClickHouse query API with cursor pagination.
- `API-003` Add resumable SSE/WebSocket realtime delivery.
- `API-004` Add detection-to-incident correlation and audit events.
- `API-005` Add asynchronous PCAP analysis jobs.
- `UI-001` Create the React/TypeScript/Vite workspace and generated API client.
- `UI-002` Build shared loading, error, empty, offline, and severity components.
- `OPS-001` Build the Security Operations Console.
- `AN-001` Build the Analytics Workbench and shared time/filter context.
- `ML-001` Replace interval inference with event-driven feature consumers.
- `ML-002` Add model artifacts, checksums, versions, and rollback in RustFS/Postgres.
- `MIG-001` Run old and new pipelines in dual-run mode.
- `MIG-002` Migrate control data and analytical history.
- `MIG-003` Cut over UI and realtime consumers.
- `MIG-004` Remove SQLite only after parity and recovery tests pass.

`SENSOR-001` is implemented by `scripts/benchmark_capture.py`; its output is
the required input for the capture-engine implementation decision.
