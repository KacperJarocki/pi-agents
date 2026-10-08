# Design

## Context

See `proposal.md` (Why) and `specs/` for required behaviour. The cluster has 5× Raspberry Pi 5 nodes, each with 8 GB RAM and NVMe storage. One node (`node-role.kubernetes.io/gateway=true`) is the Wi-Fi AP and capture point. Existing scaffolding:
- `sensors/rust-sensor` has only a bounded `FlowTable`;
- `schemas/events/v1` is shaped after the legacy buckets;
- the `pi-agents` chart has single-node Strimzi Kafka and a Flink `FlinkDeployment` with a Longhorn RWX checkpoint profile;
- Flux installs the Strimzi and Flink operators.

The legacy pipeline keeps running and is not touched.

## Goals / Non-Goals

**Goals:**
- One working path from captured packet to browser, with no fixed-interval wait anywhere on it. Target: under 2 s from the evidence-completing packet to the browser, measured from propagated timestamps rather than assumed.
- Deterministic replay: the same retained events produce the same incidents.
- Production-shaped platform (replicated Kafka, HA-capable Flink, backed-up PostgreSQL) sized for 5× 8 GB.

**Non-Goals:**
- ML scoring, model training/registry, and the 14-feature ML set (follow-up change, capability `streaming-model-lifecycle`).
- Rules other than port scan.
- Merging legacy and streaming alerts, or removing the legacy path.
- Dashboard redesign beyond one live incidents page.

## Decisions

### 1. Sensor: micro-flows with a 1-second flush

The Rust sensor captures on the AP interface (AF_PACKET via `pcap`). It aggregates packets into micro-flows keyed by `(src, dst, sport, dport, proto)` in the existing bounded `FlowTable`. A micro-flow is flushed when it has been idle for 1 s or **active for 1 s**, whichever comes first, so aggregation adds at most 1 s of latency. Each event carries `first_packet_time` and `last_packet_time` from packet timestamps.

For TCP it also carries flag counts (SYN, SYN-ACK, RST, FIN) and an `attempt_outcome`: `established`, `rejected` (RST reply), `unanswered` (SYN only before flush), or `n/a`. A flow that later establishes emits a follow-up event; the detector uses the latest outcome per micro-flow.

Device attribution uses the client MAC from the AP side, so `device_key = <gateway_id>:<mac>`. Packets without a known client MAC go to the `unattributed` health counter.

Why micro-flows rather than raw packets: they bound event volume to the number of distinct conversations per second, not packets per second. Per-port scan evidence survives because every probed port is its own 5-tuple. Alternative considered: per-packet events, which give the simplest semantics but multiply Kafka and Flink load by roughly 10–100× on streaming devices.

Publishing uses librdkafka with `acks=all`, `enable.idempotence=true` and `linger.ms ≤ 20`. Before send, every event is appended to a **bounded on-disk spool** (segmented append-only log on NVMe, default 2 GiB) and removed only after the broker acknowledges it. When the spool is full, the oldest unacknowledged segment is dropped. The sensor then emits a `HealthEvent` with the loss count and the first/last affected `capture_sequence` and times. `capture_sequence` is a per-gateway monotonic counter persisted alongside the spool, so IDs survive a restart. Event ID is `<gateway_id>/<capture_sequence>`.

### 2. Event contract v2

`schemas/events/v2/events.proto` (package `pi_agents.events.v2`) defines `Envelope`, `FlowEvent`, `FeatureUpdate`, `DetectionTransition` and `HealthEvent`. Fixed values use proto enums: protocol, direction, attempt outcome, transition status (`OPEN`, `UPDATE`, `REOPEN`, `RESOLVE`) and threat family. Consumers must tolerate unknown enum values.

`DetectionTransition` carries:
- `incident_id`, `revision`, `transition_id = incident_id:revision`;
- `threat_family`, `rule_id`, `rule_version`, and the horizons that met their threshold, each with its value and threshold;
- `evidence_event_time`, `detected_at`, and up to 20 sample source event IDs.

`v2` gets its own `compatibility.json`, `baseline.proto` and validator run. `v1` stays frozen because nothing new consumes it. Alternative considered: keep extending `v1`. It would carry bucket fields that no streaming producer fills, and string-typed statuses.

### 3. Kafka topology

Strimzi runs a `KafkaNodePool` with 3 nodes, each with combined broker and controller roles (KRaft). They are spread by pod anti-affinity over non-gateway nodes and use `local-path` PVCs on NVMe. Kafka replicates itself, so Longhorn's own replication would only multiply writes. Settings:
- `default.replication.factor=3`, `min.insync.replicas=2`;
- JVM heap 768 MiB, container limit 1.5 GiB.

Topics are created as `KafkaTopic` CRs:

| Topic | Key | Partitions | Retention |
| --- | --- | --- | --- |
| `traffic.flows.v2` | `device_key` | 6 | 14 days |
| `traffic.features.v2` | `device_key` | 6 | 3 days |
| `traffic.detections.v2` | `incident_id` | 3 | 30 days |
| `traffic.health.v2` | `gateway_id` | 1 | 14 days |
| `traffic.late.v2` | `device_key` | 1 | 7 days |

Changing the partition count of `traffic.flows.v2` requires a replay plan; it is never done in place.

### 4. Flink job: rolling views and port-scan rule

The job is a Java 17 DataStream job built as its own image from `flink:1.20.x-java17` (ARM64). It runs `KafkaSource` → dedupe → `keyBy(device_key)` → `KeyedProcessFunction` → `KafkaSink`s. Settings:
- state backend `hashmap`: state is small, and on ARM it is faster than RocksDB;
- checkpoints every 10 s to RustFS S3, Kafka sink in exactly-once (transactional) mode;
- 1 JobManager (1.5 GiB) and 2 TaskManagers (2 GiB, 2 slots each), parallelism 4, kept off the gateway node.

**Per-device state, per horizon (60 s and 900 s):** a time-ordered deque of contributions plus incremental aggregates: flow count, bytes, distinct destination IPs, and a map from `(dst_ip, dst_port)` to the latest event time, with an outcome flag. Expiry pops from the deque head and decrements the aggregates. The state also keeps a dedupe set of event IDs seen within `900 s + lateness`.

**Clock and silence:** the device clock is the maximum event time seen. A processing-time timer fires 1 s after the last change and then every 5 s while the state is non-empty. On each firing it advances the device clock by the elapsed wall time since the last event, then expires evidence. As a result, views and detections update during silence without relying on a global watermark. Lateness allowance: 30 s behind the device clock. Events later than that go to `traffic.late.v2`. Events inside the allowance are inserted in order and the aggregates are recomputed for that horizon.

**Port-scan rule v1:**
- Signal = number of distinct `(dst_ip, dst_port)` targets in the horizon whose outcome is `rejected` or `unanswered`.
- Open thresholds (configurable): 60 s ≥ 20 targets, or 900 s ≥ 40 targets.
- Clear threshold: 50 % of the open threshold in **both** horizons, sustained for 60 s (hold).
- Reopen cooldown: 300 s.
- The rule is evaluated after every state change. Revisions are emitted only when status, the set of horizons over threshold, or severity bucket changes. This prevents a revision on every packet.

`incident_id` is a hash of `device_key`, `threat_family` and the event time of the episode's first threshold-crossing event, so replay reproduces it.

**Feature updates** go to `traffic.features.v2` for observability. They are emitted at most once per second per device and horizon; this limit does not affect rule evaluation.

Alternative considered: Flink sliding windows (`SlidingEventTimeWindows`). They emit only at slide boundaries and duplicate state for each slide, which contradicts evaluation on every event.

### 5. Incident projector and PostgreSQL

PostgreSQL runs on CloudNativePG: 2 instances (primary + replica) on `local-path` NVMe, 2 GiB limit each, with barman-cloud backups to RustFS.

The projector is a small Python service using `confluent-kafka` and `psycopg`, with auto-commit disabled. For each transition it runs one transaction:
1. `INSERT INTO incident_transitions (transition_id PK, …) ON CONFLICT DO NOTHING`; if nothing was inserted, skip the remaining steps;
2. upsert into `incidents`, but only where the incoming revision is newer than the stored one;
3. `INSERT INTO incident_feed (cursor bigserial, incident_id, revision, payload)`;
4. `NOTIFY incident_feed, '<cursor>'`.

After the commit, it commits the Kafka offset. Duplicates and replays become no-ops at the transition table. The feed is retained for 7 days; older cursors fall back to a snapshot.

Alternative considered: a Flink JDBC sink. It would couple Flink checkpoints to Postgres availability and make the NOTIFY step awkward.

### 6. Incident API and browser

`incident-api` is a new FastAPI service:
- `GET /api/v2/incidents?active=true` returns a snapshot plus its cursor;
- `GET /api/v2/incidents/changes?after=<cursor>` returns catch-up changes;
- `WS /ws/v2/incidents?after=<cursor>` sends catch-up first and then live changes from `LISTEN incident_feed`;
- connection health and the latest `HealthEvent` summary go out on the same socket.

The dashboard gets a `/live` page (Alpine.js) that keeps one row per `incident_id`, applies revisions in order, stores the last cursor, and reconnects with backoff. It is proxied through the existing dashboard like other `/api/*` routes.

To measure the latency target, each transition and feed row carries `evidence_event_time`, flow `ingest_time`, `detected_at`, `committed_at`, and the browser receive time. The page can log the end-to-end delay.

### 7. Placement and resources

| Node | Workloads | Approx. limits |
| --- | --- | --- |
| gateway | sensor (+ 2 GiB spool on hostPath NVMe), legacy gateway pods | 0.5 GiB |
| node-a | Kafka-0, Flink JobManager, PG replica | 1.5 + 1.5 + 2 GiB |
| node-b | Kafka-1, Flink TM-0 | 1.5 + 2 GiB |
| node-c | Kafka-2, Flink TM-1 | 1.5 + 2 GiB |
| node-d | PG primary, RustFS, projector, incident-api | 2 + 1 + 0.25 + 0.25 GiB |

Placement uses anti-affinity and preferred node affinity, not hard pins, except for two rules: the sensor is required on the gateway node, and every streaming component is excluded from it. The Flux HelmReleases add the operators: Strimzi (already present), Flink operator (already present) and CloudNativePG (new).

## Risks / Trade-offs

- [Single RustFS node holds checkpoints and backups] → Acceptable at this stage. Kafka retention still allows a job restart from offsets. Revisit with distributed RustFS later.
- [Unanswered-SYN outcome is provisional at flush time] → The follow-up `established` event retracts that target from the signal. Fixtures cover slow servers.
- [Threshold values are guesses] → They are configurable per rule version. Validate them against the existing `negative`/`borderline`/`positive`/`slow`/`aggressive` port-sweep profiles before trusting alerts.
- [Processing-time device clock drifts from event time under broker lag] → Expiry during silence is approximate. Event-driven updates stay exact, and lag is exported as a metric.
- [Exactly-once Kafka sink adds checkpoint-interval latency for `read_committed` consumers] → The projector reads `traffic.detections.v2` with `read_uncommitted` and relies on transition-level idempotence. This keeps it off the 10 s checkpoint path.
- [Clock offset between gateway and cluster nodes skews latency numbers] → Require chrony on all nodes and export the offset.

## Migration Plan

1. Deploy the platform (Kafka node pool, CloudNativePG, RustFS, Flink with the S3 profile) with `streaming.enabled=true` alongside the legacy stack.
2. Deploy the sensor in parallel with the legacy collector on the gateway. Both capture independently.
3. Deploy the Flink job, projector, incident API and `/live` page. Validate against the port-sweep profiles.
4. Rollback: disable `streaming.enabled`. Legacy is unaffected. Kafka and PostgreSQL data are retained.
5. Cutover and removal of the legacy path are out of scope for this change.
