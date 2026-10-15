# Detection job

Flink 1.20 (Java 17) DataStream job that reads `traffic.flows.v2` and, per
device, will maintain the 60 s / 900 s sliding views and the port-scan rule
(`openspec/changes/realtime-sliding-threat-detection`). Implemented so far:

- **Decoding**: v2 `FlowEvent` classes are generated from
  `schemas/events/v2/events.proto` at build time (`protobuf-maven-plugin`; no
  local `protoc`). Records travel as a POJO (`FlowRecord`), not as Protobuf
  messages, so Flink never falls back to Kryo. Undecodable values, or events
  without event ID, device key or event time, are routed as `malformed`
  instead of failing the job.
- **Dedupe**: keyed by device key; event IDs are remembered for the longest
  horizon plus the lateness (900 s + 30 s, state TTL) and repeats are dropped
  (`duplicateEvents` metric).
- **Lateness**: the device clock is the maximum event time seen; events more
  than 30 s behind it go to `traffic.late.v2` with header
  `pi-agents-reason: too_late` (`lateEvents` metric). Malformed events go to
  the same topic with `pi-agents-reason: malformed`. Key = device key, value =
  the original bytes.

- **Rolling views** (`DeviceWindows`, `RollingViews`): per device, one
  time-ordered deque per horizon (60 s and 900 s) with incremental aggregates:
  flows, bytes, distinct remote IPs, distinct `(protocol, IP, port)` targets,
  and *failed targets* (targets whose latest attempt was rejected or
  unanswered; a later `established` retracts the target). A horizon covers
  `(clock - horizon, clock]`; events late within the 30 s allowance are
  inserted in order. The device clock is the maximum event time; during
  silence a processing-time timer (1 s after the last change, then every 5 s)
  advances it by the wall time since the last event, so evidence expires
  without new packets. State is cleared once every horizon is empty. A
  `ViewSet` (all horizons) is emitted whenever any horizon changes, so the
  rule evaluates once per change.
- **Port-scan rule v1** (`PortScanRule`, `PortScanDetector`): signal = failed
  targets per horizon. Opens at 60 s ≥ 20 or 900 s ≥ 40; resolves when both
  horizons stay at or below 50 % of their thresholds for 60 s of device time
  (a timer keeps time moving without new views); a crossing within 300 s of a
  resolve reopens the same incident. Revisions only when the status, the set
  of horizons over threshold or the severity bucket (signal/threshold ≥ 2
  medium, ≥ 4 high) changes. `incident_id` = first 16 bytes of
  SHA-256(`device_key|port_scan|<event time of the first crossing>`), so a
  replay reproduces it; `transition_id` = `<incident_id>:<revision>`.
  Transitions go to `traffic.detections.v2` (key = incident ID) as
  `DetectionTransition` through an exactly-once Kafka sink
  (`transaction.timeout.ms` 15 min, within the broker default).
- **Serialization**: generic types are disabled; records and views are POJOs
  and the window state has its own `DeviceWindowsSerializer` (aggregates are
  rebuilt on restore), so nothing goes through Kryo.

## Build and test

```sh
cd flink/detection-job
./mvnw verify          # unit + MiniCluster tests, shaded jar in target/
```

Maven comes from the wrapper (`.mvn/wrapper`, Maven 3.9.16); JDK 17 is needed.

## Image

```sh
docker build -f flink/detection-job/Dockerfile .   # from the repository root
```

The image is `flink:1.20.5-scala_2.12-java17` with the job at
`/opt/flink/usrlib/detection-job.jar`; `docker-build.yml` publishes it as
`ghcr.io/kacperjarocki/flink-detection-job` for amd64 and arm64.

Arguments: `--bootstrap` (default `pi-agents-kafka-bootstrap:9092`),
`--group-id` (default `pi-agents-detection`), `--lateness-ms` (default 30000),
`--open-threshold-60s` (20), `--open-threshold-900s` (40), `--clear-fraction`
(0.5), `--hold-ms` (60000), `--cooldown-ms` (300000),
`--transactional-id-prefix` (`pi-agents-detection`).
