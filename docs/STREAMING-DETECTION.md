# Streaming detection

How the Flink job in `flink/detection-job` turns `traffic.flows.v2` into port-scan incidents. The plan and requirements are in `openspec/changes/realtime-sliding-threat-detection/`.

```
traffic.flows.v2 ─► validate ─► dedupe + lateness ─► rolling views ─► port-scan rule ─► traffic.detections.v2
                       │               │                    │
                       └── malformed ──┴── too late ──► traffic.late.v2
                                                           └─► feature throttle ─► traffic.features.v2
```

## Event time and the device clock

- Every flow event carries the packet time of its last packet (`Envelope.event_time_unix_millis`). Records are keyed by `device_key` (`<gateway_id>:<client MAC>`), so one device's events are processed in Kafka partition order.
- The **device clock** is the maximum event time seen for that device. There is no global watermark: a quiet device does not hold back others.
- **Lateness**: an event more than 30 s behind the device clock goes to `traffic.late.v2` (header `pi-agents-reason: too_late`). Events inside the allowance are inserted in event-time order and update every horizon they still fall into.
- **Duplicates**: event IDs (`<gateway_id>/<capture_sequence>`) are remembered for 930 s (longest horizon + lateness); a repeated ID is dropped, so sensor resends after restarts are not counted twice.
- **Silence**: a processing-time timer (1 s after the last change, then every 5 s) moves the device clock forward by the wall time since the device's last event, so evidence expires and incidents can resolve without new packets. When broker lag delays events, expiry during silence is approximate; event-driven updates stay exact.

## Rolling views

Per device and horizon (60 s and 900 s), the window is `(clock − horizon, clock]`. Aggregates are maintained incrementally as flows enter and leave:

| Feature | Meaning |
| --- | --- |
| `flows` | Micro-flows in the window |
| `bytes` | Bytes in both directions |
| `distinct_remote_ips` | Distinct remote IPs |
| `distinct_targets` | Distinct `(protocol, remote IP, remote port)` targets |
| `failed_targets` | Distinct targets whose **latest** attempt was rejected (RST / ICMP unreachable) or unanswered (SYN only) |

A later `established` attempt to the same target removes it from `failed_targets`; older evidence that arrives late never overrides newer evidence. Views are emitted (as one set covering both horizons) whenever any aggregate changes; the rule reads every change, while `traffic.features.v2` gets at most one `FeatureUpdate` per device and horizon per second.

## Port-scan rule v1

| Parameter | Default | Job argument / chart value |
| --- | --- | --- |
| Open threshold, 60 s horizon | 20 failed targets | `--open-threshold-60s` / `flink.detectionJob.rule.openThreshold60s` |
| Open threshold, 900 s horizon | 40 failed targets | `--open-threshold-900s` / `…openThreshold900s` |
| Clear level | 50 % of each open threshold | `--clear-fraction` / `…clearFraction` |
| Hold before resolve | 60 s of device time | `--hold-ms` / `…holdMs` |
| Reopen cooldown | 300 s | `--cooldown-ms` / `…cooldownMs` |
| Severity buckets | signal ÷ threshold ≥ 2 medium, ≥ 4 high | — |

- **Open** when either horizon reaches its threshold. The evidence time is the event time that completed the evidence; the episode start is that time too.
- **Update** when the set of horizons over threshold or the severity bucket changes. Count changes inside a bucket produce no revision.
- **Resolve** when both horizons stay at or below the clear level for the hold time.
- **Reopen**: a crossing within the cooldown after a resolve continues the same incident; later crossings open a new one.
- `incident_id` = first 16 bytes (hex) of SHA-256(`device_key|port_scan|<episode start>`); `transition_id` = `<incident_id>:<revision>`. Replaying the same flows yields the same IDs.

Each `DetectionTransition` carries the per-horizon value, threshold and over-threshold flag, up to 20 newest failed-attempt event IDs, the evidence event time, the ingest time of the evidence-completing flow and the detection time (for end-to-end latency).

The thresholds are first guesses. Validate them against the port-sweep research profiles (`negative`, `borderline`, `positive`, `slow`, `aggressive`) before trusting alerts; record the results below.

## Delivery guarantees

| Output | Guarantee | Notes |
| --- | --- | --- |
| `traffic.detections.v2` | Exactly-once (Kafka transactions committed with checkpoints) | The incident projector reads `read_uncommitted` and dedupes by `transition_id`, so it does not wait for the 10 s checkpoint interval. `transaction.timeout.ms` = 15 min, within the broker default. |
| `traffic.late.v2` | At-least-once | Original bytes; reason and detail headers. |
| `traffic.features.v2` | At-least-once | Observability only. |

Checkpoints and savepoints go to `s3://pi-agents/flink/` on RustFS; the `FlinkDeployment` uses `upgradeMode: savepoint`, so a job upgrade takes a savepoint and restores from it. Every stateful operator has a stable `uid`.

## Observability

Flink metrics (per operator):

| Metric | Operator | Meaning |
| --- | --- | --- |
| `duplicateEvents` | dedupe-and-lateness | Repeated event IDs dropped |
| `lateEvents` | dedupe-and-lateness | Events sent to `traffic.late.v2` as too late |
| `contributionsPerDevice` (histogram) | rolling-views | Window state size per device |
| `transitions` | port-scan-rule | Detection transitions emitted |

Sensor health (loss, unattributed traffic, flow-table evictions, heartbeat) arrives on `traffic.health.v2` from the gateway.

## Deploying

Set `flink.detectionJob.enabled=true` and `flink.detectionJob.image.tag` to a `sha-<8 chars>` tag of `ghcr.io/kacperjarocki/flink-detection-job`. When an existing deployment switches from the smoke job, also set `flink.detectionJob.allowNonRestoredState=true` for that rollout (the smoke job's savepoint holds state the detection job does not have), then set it back to `false`.

## Results (task 7.1)

To be filled from the port-sweep runs: incidents opened, false positives/negatives and evidence-to-browser delay per profile, with the threshold values used.
