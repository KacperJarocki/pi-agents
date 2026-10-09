# Streaming Platform Roadmap

This document is the execution backlog for the production real-time analytics
rewrite. The SQLite/batch pipeline from the thesis is the legacy path. It is
removed directly, without dual-run, after tagging its last state as
`thesis-final` (see the `MIG-*` tasks). The active OpenSpec change is
`openspec/changes/realtime-sliding-threat-detection`.

## Definition of Ready

A task is ready when its scope, dependencies, interfaces, resource budget,
acceptance criteria, test plan, rollout plan, and rollback plan are documented.
Any required architecture decision must be accepted before implementation.

## Definition of Done

A task is done when its implementation is reviewed, tested, observable,
ARM64-compatible, configured with resource requests and limits, documented, and
verified in the target Kubernetes environment. Failure modes and rollback must
be covered. Only the `MIG-*` tasks remove legacy code or workloads.

## Milestones

| ID | Outcome |
| --- | --- |
| M0 | Capacity measurements, ADRs, event contracts, and SLOs |
| M1 | Kafka, Flink, RustFS, PostgreSQL, and ClickHouse platform |
| M2 | Rust sensor with Kafka publishing and PCAP ring |
| M3 | Stateful Flink features and sub-two-second fast detections |
| M4 | Operations UI with live alerts and incident workflow |
| M5 | Analytics Workbench and PCAP analysis jobs |
| M6 | Event-driven ML and model lifecycle |

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
- `SENSOR-007` Validate sensor output against the port-sweep research profiles.

### M3: Analytics

- `FLINK-001` Validate, deduplicate, and route malformed events to a DLQ.
- `FLINK-002` Add event-time watermarks and late-event side outputs.
- `FLINK-003` Add non-blocking enrichment and threat-intelligence broadcast state.
- `FLINK-004` Reproduce the existing feature set with golden datasets.
- `FLINK-005` Maintain per-device 60-second and 900-second rolling views, updated on every event rather than at fixed slide boundaries.
- `FLINK-006` Add CEP/rule detections for scans, bursts, beacons, and IOC traffic.
- `FLINK-007` Publish features, detections, and pipeline health topics.
- `FLINK-008` Test checkpoint recovery, backpressure, and broker loss.

### M4-M6: Serving, UI, and ML

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

### Legacy removal

These tasks run before the streaming platform is deployed. Detection is
unavailable until the streaming path is live.

- `MIG-001` Tag `main` as `thesis-final` and push the tag.
- `MIG-002` Delete the legacy images, manifests, Compose services, tests and CI jobs; keep `gateway-agent`.
- `MIG-003` Rewrite `AGENTS.md`, `CLAUDE.md` and `README.md` for the streaming system.
- `MIG-004` Remove the legacy workloads and SQLite PVCs from the cluster.

`SENSOR-001` is implemented by `scripts/benchmark_capture.py`; its output is
an optional hardware baseline for the capture-engine implementation decision.

## Event path contract (first opt-in release)

The new pipeline uses `schemas/events/v2/events.proto`; `v1` is frozen. A `FlowEvent` has an
immutable `event_id` derived from `(gateway_id, capture_sequence)`, packet
`event_time_unix_millis`, first publish `ingest_time_unix_millis`, and a stable
`device_key` (gateway ID plus the client MAC when known). Replaying an event
preserves its ID and timestamps. Flink partitions by `device_key` to keep
per-device updates ordered; two devices need not share a global order.

| Topic | Key | Partitions | Retention |
| --- | --- | --- | --- |
| `traffic.flows.v2` | `device_key` | 6 | 14 days |
| `traffic.features.v2` | `device_key` | 6 | 3 days |
| `traffic.detections.v2` | `incident_id` | 3 | 30 days |
| `traffic.health.v2` | `gateway_id` | 1 | 14 days |
| `traffic.late.v2` | `device_key` | 1 | 7 days |

None of the topics is compacted. The incident projector stores current incident
state in PostgreSQL.

Use the same stable key for all events from a given device and do not change
the partition count of `traffic.flows.v2` without a replay and state-migration
plan. Kafka guarantees order **within** a partition, not
event-time order or exactly-once effects at an external database. Configure
three brokers with replication factor 3 and minimum in-sync replicas 2;
producers use `acks=all`, idempotence, bounded retries and a durable gateway
spool until broker acknowledgment. Consumers checkpoint offsets together with
window state, and the incident projector deduplicates transition IDs in the
same transaction that updates the incident. On insufficient ISR or broker
unavailability, the gateway queues locally instead of reporting success.

Retention is independent of consumer progress. Topic/storage quotas and spool
limits MUST be explicit and monitored; if the backlog reaches its bound, emit a
health event containing the first/last affected sequence and loss count. A
longer configured training lookback requires at least that much retained raw
history (plus recovery margin), or a separately validated archive. Do not
claim full research coverage when earlier offsets have expired. Record the
observed queue lag and available retention before extending lookback.

Flink updates the rolling 60- and 900-second views immediately on arrival;
watermarks and an initial 30-second lateness allowance are for event-time
corrections, **not** a 30-second hold before first scoring. Events arriving
within that allowance may revise the same incident; later events are sent to
the late-event side output (`traffic.late.v2`) with reason and identity. Replayed IDs must not
increment counters twice. Per-device state retains only the maximum horizon
plus lateness, along with bounded deduplication state; TTL and state-size
alerts report when evidence is incomplete.

Deterministic ordering example (same device, window at 12:00:03):

| Kafka arrival | Event ID | Packet time | Expected result |
| --- | --- | --- | --- |
| 1 | `gw/41` | 12:00:01 | Add once to both horizons. |
| 2 | `gw/42` | 12:00:03 | Update both horizons and score immediately. |
| 3 | `gw/41` | 12:00:01 | Duplicate: no counter or incident revision change. |
| 4 | `gw/40` | 12:00:02 | Out-of-order but within lateness: correct both horizons without a second incident. |
| 5 | `gw/12` | 11:40:00 | Older than both horizons and allowed lateness: record as late, not as a new detection. |

Use this fixture for replay tests in `SENSOR-005`, `FLINK-002` and
`FLINK-008`. It illustrates ordering and correction semantics without claiming
that packet timing or throughput has been measured on the gateway.
