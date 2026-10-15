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
`--group-id` (default `pi-agents-detection`), `--lateness-ms` (default 30000).
