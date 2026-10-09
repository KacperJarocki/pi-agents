# Runtime Configuration

The platform uses explicit service endpoints so stateful services can run
inside or outside the Kubernetes cluster. `DATABASE_PATH` remains a legacy
SQLite fallback while the migration is in dual-run mode.

| Variable | Purpose | Required for streaming runtime |
| --- | --- | --- |
| `CONTROL_DATABASE_URL` | PostgreSQL control-plane connection | Yes |
| `DATABASE_PATH` | legacy SQLite fallback | No |
| `CLICKHOUSE_URL` | ClickHouse analytics endpoint | Yes for analytics queries |
| `CLICKHOUSE_DATABASE` | ClickHouse database name | No, defaults to `analytics` |
| `OBJECT_STORAGE_ENDPOINT` | RustFS/S3-compatible endpoint | Yes for PCAP/checkpoints |
| `OBJECT_STORAGE_BUCKET` | Object storage bucket/prefix root | No, defaults to `pi-agents` |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker list | Yes for event streaming |
| `EVENT_SCHEMA_VERSION` | Protobuf event contract version | No, defaults to `v2` |

The application must not infer whether a service is internal or external from
its hostname. Helm values or environment injection own that decision.
