# Proposal

## Why

The current collector, five-minute closed buckets, periodic inference, and 30-second dashboard polling delay notifications long after the traffic that justified them. The thesis results favor sliding windows, including a longer horizon for slow attacks; we need to react promptly to new evidence without shortening the observation horizon.

## What Changes

- Add a packet-time, versioned traffic event path from the gateway sensor into the existing opt-in Kafka platform, while retaining the legacy collector during migration.
- Maintain continuously updated, per-device sliding windows of at least 1 and 15 minutes in Flink; evaluate detection on new evidence rather than waiting for a bucket boundary or polling interval.
- Detect port scans with a versioned, configurable rule (port-scan v1) evaluated on both horizons. ML scoring and periodic window-compatible model training follow in a separate change.
- Publish correlated, durable detection state changes and deliver them to the dashboard immediately, with reconnect/replay on a dedicated live incidents view; the legacy alert feed stays unchanged.
- Deliver notifications without fixed-bucket or polling delays, aiming for under 2 seconds from the evidence-completing packet to the browser. Run alongside the legacy path; cutover and legacy removal are decided in a later change after dual-run, compatibility and recovery tests. Hardware latency benchmarking is not a prerequisite.

## Capabilities

### New Capabilities

- `traffic-event-ingestion`: Timestamped, durable, replayable gateway traffic events and ingestion health.
- `sliding-window-detection`: Continuously updated multi-horizon features and timely, deduplicated threat decisions.
- `live-threat-notifications`: Durable incident updates and low-latency, resumable browser delivery.

Deferred to a follow-up change: `streaming-model-lifecycle`.

### Modified Capabilities

None; there are no existing OpenSpec capability specs.

## Impact

- Gateway collector/sensor, event schema, Kafka/Strimzi Helm configuration, new Flink jobs and deployment, gateway API, dashboard, research/backtest tooling, and CI/e2e tests.
- Preserve the current SQLite/batch path during dual-run; integrate with the existing Kafka opt-in scaffolding and event envelope instead of introducing a second broker.
- Revisit roadmap assumptions that prescribe 1-second/10-second fast-path windows: the product baseline here is 1-minute and 15-minute **sliding** windows, updated on incoming evidence. Other horizons remain configurable after validation against thesis results.
