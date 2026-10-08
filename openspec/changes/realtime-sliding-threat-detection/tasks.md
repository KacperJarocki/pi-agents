# Tasks

## 1. Contract and roadmap

- [x] 1.1 Document the current capture, bucket, inference and dashboard delay sources from code/config in `docs/CAPTURE-BENCHMARK.md`; verify all timings are identified as configuration values rather than measured results.
- [x] 1.2 Add `schemas/events/v2/events.proto` (`Envelope`, `FlowEvent`, `FeatureUpdate`, `DetectionTransition`, `HealthEvent`, enums) with `compatibility.json` and `baseline.proto`; extend `scripts/validate_event_contract.py` to validate v1 and v2. Verify with `python -m unittest tests.test_event_contract_validator -v`.
- [x] 1.3 Update `docs/STREAMING-ROADMAP.md`: replace `FLINK-005` (1 s/10 s) with 60 s/900 s rolling horizons, switch the topic table to the v2 topics, keys, partitions and retention from design.md, and replace the dual-run/cutover milestones (`MIG-*`, M6 SQLite retirement) with the direct legacy removal. Verify with `grep -n "FLINK-005" docs/STREAMING-ROADMAP.md`, then run the source-assertion tests.

## 2. Legacy removal

- [x] 2.1 Tag `main` as `thesis-final` and push the tag. Verify with `git ls-remote --tags origin thesis-final`.
- [x] 2.2 Delete `images/{collector,ml-pipeline,gateway-api,dashboard}`, their `k8s/gateway` manifests, the SQLite/model PVCs in `k8s/base`, and their `docker-compose.yml` services and volumes; keep `gateway-agent` and `k8s/overlays/gateway-prod`. Verify that `kubectl kustomize k8s/gateway` and `kubectl kustomize k8s/overlays/gateway-prod` render only `gateway-agent` resources and `docker compose config` succeeds.
- [ ] 2.3 Delete the tests that import or assert on removed images, the legacy Playwright specs and fixtures in `tests/ui` (keeping the harness), and the matching `validate.yml` steps and `docker-build.yml` matrix entries. Verify with `python -m compileall -q images` and `python -m unittest discover -v` passing locally, and green CI on the PR.
- [x] 2.4 Rewrite `AGENTS.md`, `CLAUDE.md` and `README.md` for the streaming system and `gateway-agent` only. Verify that `grep -rnE "collector|ml-pipeline|gateway-api|images/dashboard|SQLite" AGENTS.md CLAUDE.md README.md` returns only intentional historical references to `thesis-final`.
- [ ] 2.5 Remove the legacy workloads from the cluster (`kubectl delete` of the removed manifests, then the PVCs). Verify that `kubectl get all,pvc -n iot-security` shows only `gateway-agent` and streaming resources.

## 3. Platform

- [ ] 3.1 Replace the single-node Kafka with a 3-node combined KRaft `KafkaNodePool` (RF3, min ISR 2, `local-path`, anti-affinity, off gateway, 1.5 GiB limit) and add `KafkaTopic` CRs for the v2 topics. Verify with `helm lint` / `helm template` and, in K3s, by producing to a topic with one broker pod deleted.
- [ ] 3.2 Add a RustFS deployment (`local-path`, resource limits, bucket bootstrap, credentials Secret reference only). Make the S3 checkpoint profile the default Flink profile, with TM 2 GiB / 2 slots ×2 and JM 1.5 GiB. Verify with `helm lint charts/pi-agents -f charts/pi-agents/values-flink-s3.yaml` and a running smoke job that restores from checkpoint after its TaskManager is deleted.
- [ ] 3.3 Add the CloudNativePG operator HelmRelease to `k8s/flux` and a 2-instance `Cluster` with barman-cloud backups to RustFS. Verify with `kubectl kustomize k8s/flux`, `helm template`, and in K3s a primary failover plus a completed backup.

## 4. Gateway sensor

- [ ] 4.1 Implement capture on the AP interface with MAC-based device attribution, micro-flow aggregation with 1 s idle/active flush, TCP flag counts and attempt outcome. Verify with pcap fixture tests for TCP established/rejected/unanswered, UDP, ICMP, DNS, unattributed traffic, and packet-time timestamps.
- [ ] 4.2 Add the v2 Protobuf encoding, persisted `capture_sequence`, the bounded segmented spool, and the idempotent `acks=all` producer. Verify with tests for broker outage and recovery (ordered, no loss), restart without ID reuse, and spool exhaustion emitting a `HealthEvent` with loss count and range.
- [ ] 4.3 Add the sensor container image (ARM64), the DaemonSet pinned to the gateway node with capabilities, a hostPath spool, resource limits and health metrics, and setup docs in `sensors/rust-sensor/README.md`. Verify with `helm template` and, in K3s, events from a test client appearing on `traffic.flows.v2`.

## 5. Flink detection job

- [ ] 5.1 Create the Flink job module (Java 17, Maven, ARM64 image) with v2 Protobuf deserialisation, event-ID dedupe and a `traffic.late.v2` side output. Verify with MiniCluster tests for the duplicate and too-late cases from the roadmap ordering fixture.
- [ ] 5.2 Implement the per-device 60 s/900 s rolling state with incremental aggregates, in-order insertion within the 30 s lateness, and processing-time expiry during silence. Verify with tests for burst, sparse device, out-of-order correction, silence expiry, and a long-running stream with bounded state size.
- [ ] 5.3 Implement port-scan rule v1 with configurable open/clear thresholds, hold, cooldown and deterministic `incident_id`, emitting `DetectionTransition`s only on meaningful change. Verify fixture tests: a fast scan opens via 60 s; a slow scan opens via 900 s only; benign traffic opens nothing; oscillation yields one incident; resolve after hold; reopen within cooldown; replay from scratch yields identical IDs.
- [ ] 5.4 Emit throttled `FeatureUpdate`s, add the `FlinkDeployment` job spec to the chart (exactly-once sink, S3 checkpoints), and document window semantics, rule parameters and observability in `docs/STREAMING-DETECTION.md`. Verify with `helm template` and, in K3s, the job recovering from a savepoint without duplicate transitions.

## 6. Incidents and browser

- [ ] 6.1 Add the Postgres schema migrations (`incident_transitions`, `incidents`, `incident_feed`) and the projector service with a transactional dedupe, revision guard, `NOTIFY` and post-commit offset commit. Verify with tests against a real Postgres: repeated delivery, stale revision, a crash between DB commit and offset commit, and feed pruning.
- [ ] 6.2 Implement `incident-api` (snapshot, changes-after-cursor, WebSocket with catch-up then live, health summary) with an image, chart templates and resource limits. Verify with tests for reconnect with a cursor, an expired cursor falling back to snapshot, and ordering across catch-up and live.
- [ ] 6.3 Serve the static `/live` page from `incident-api` with an `Ingress` on the former dashboard host: one row per incident, revision application, a stored cursor, reconnect with backoff, and banners for lost connection and reported ingestion loss. Verify with mocked Playwright tests in `tests/ui/live.spec.ts`.

## 7. End-to-end

- [ ] 7.1 Run the `negative`, `borderline`, `positive`, `slow` and `aggressive` port-sweep profiles through the deployed stack. Record per profile: incidents opened, false positives/negatives, and the delay from evidence event time to browser receipt. Verify that `docs/STREAMING-DETECTION.md` contains the results table and the threshold values used.
- [ ] 7.2 Exercise a broker pod loss, a sensor restart during a broker outage, a Flink TaskManager kill, a Postgres failover and a browser reconnect during an active scan. Verify that each case ends with exactly one incident per scan, correct final state and no silent loss.
