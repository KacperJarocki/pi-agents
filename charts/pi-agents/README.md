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

## Kafka Cluster

Kafka is installed through the Strimzi operator. Apply the Flux bootstrap
resources once; Flux installs the operator first and then this chart:

```sh
kubectl apply -f k8s/flux/streaming-source.yaml
kubectl apply -f k8s/flux/streaming-kustomization.yaml
```

The Kafka profile uses three KRaft dual-role nodes, local-path storage, a
2GiB volume per node, and resource limits suitable for the current ARM64
cluster. Do not enable it while the node-local storage path or Longhorn is
degraded. Production retention and larger volumes belong in a separate values
file after the capacity benchmark.

The referenced Secret is managed outside this chart. The chart never stores
database credentials, S3 credentials, or ClickHouse credentials in values.
