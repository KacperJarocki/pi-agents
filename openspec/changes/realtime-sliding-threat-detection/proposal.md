# Proposal

## Why

The current collector, five-minute closed buckets, periodic inference, and 30-second dashboard polling delay notifications long after the traffic that justified them. The thesis results favor sliding windows, including a longer horizon for slow attacks; we need to react promptly to new evidence without shortening the observation horizon.

The thesis is finished, so the legacy batch pipeline is a completed proof of concept rather than a system to migrate. Keeping it alongside the new path would mean maintaining two stacks with no parity requirement, so this change replaces it outright.

## What Changes

- Add a packet-time, versioned traffic event path from the gateway sensor into the existing Kafka platform.
- Maintain continuously updated, per-device sliding windows of at least 1 and 15 minutes in Flink; evaluate detection on new evidence rather than waiting for a bucket boundary or polling interval.
- Detect port scans with a versioned, configurable rule (port-scan v1) evaluated on both horizons. ML scoring and periodic window-compatible model training follow in a separate change.
- Publish correlated, durable detection state changes and deliver them to the browser immediately, with reconnect/replay on a minimal live incidents page served by the new incident API.
- Deliver notifications without fixed-bucket or polling delays, aiming for under 2 seconds from the evidence-completing packet to the browser. Hardware latency benchmarking is not a prerequisite.
- **BREAKING** Remove the legacy batch pipeline with no dual-run: `collector`, `ml-pipeline`, `gateway-api`, `dashboard`, SQLite storage, their Kubernetes manifests and Compose services, their unit/source-assertion/Playwright tests, and their CI jobs. Tag the last legacy state as `thesis-final` first. `gateway-agent` (the Wi-Fi AP controller) stays because the sensor captures on its interface.

## Capabilities

### New Capabilities

- `traffic-event-ingestion`: Timestamped, durable, replayable gateway traffic events and ingestion health.
- `sliding-window-detection`: Continuously updated multi-horizon features and timely, deduplicated threat decisions.
- `live-threat-notifications`: Durable incident updates and low-latency, resumable browser delivery.

Deferred to a follow-up change: `streaming-model-lifecycle`.

### Modified Capabilities

None; there are no existing OpenSpec capability specs.

## Impact

- Gateway sensor, event schema, Kafka/Strimzi Helm configuration, new Flink jobs and deployment, incident projector and API, live page, research/backtest tooling, CI/e2e tests, and the removed legacy images, manifests, tests and docs (`AGENTS.md`, `CLAUDE.md`, `README.md`).
- Remove the legacy SQLite/batch path; its last state stays reachable at git tag `thesis-final`. Integrate with the existing Kafka scaffolding and event envelope instead of introducing a second broker.
- Revisit roadmap assumptions that prescribe 1-second/10-second fast-path windows: the product baseline here is 1-minute and 15-minute **sliding** windows, updated on incoming evidence. Other horizons remain configurable after validation against thesis results.
