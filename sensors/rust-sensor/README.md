# Rust Sensor Core

This crate currently contains the bounded flow state used by the future
capture/Kafka sensor. Packet capture, Protobuf serialization, and publishing
are intentionally separate components so the state machine can be benchmarked
and tested without privileged access.

Run locally:

```sh
cargo test --manifest-path sensors/rust-sensor/Cargo.toml
```

The `FlowTable` has a hard capacity. Overflow is observable through
`UpsertResult::Evicted`; callers must expose that as a metric and health event.
