# pi-agents Helm Chart

The chart currently owns the shared streaming configuration contract. It is
deliberately opt-in: `streaming.enabled` defaults to `false` until the dual-run
milestone is complete.

Render the internal profile:

```sh
helm lint charts/pi-agents
helm template pi-agents charts/pi-agents --namespace iot-security
```

Render the external storage profile:

```sh
helm template pi-agents charts/pi-agents \
  --namespace iot-security \
  --values charts/pi-agents/values-external.yaml
```

The referenced Secret is managed outside this chart. The chart never stores
database credentials, S3 credentials, or ClickHouse credentials in values.
