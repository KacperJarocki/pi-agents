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

## Flink and checkpoint storage

Flink is opt-in (`flink.enabled=false`). The Flink Kubernetes Operator is
installed separately from the pinned Apache 1.16.1 chart; its Flux
HelmRepository and suspended HelmRelease live under `k8s/flux/`. Unsuspend
the operator release and wait for the `FlinkDeployment` CRD and operator to
be ready before enabling the Flink profile in this chart. The Flink 1.20.5
runtime image includes `linux/arm64`. JobManager and TaskManagers cannot be
scheduled on the gateway-labeled node, and both have CPU/memory limits.

The initial checkpoint profile uses a **Longhorn RWX** claim mounted at
`/flink-state` by both pod roles. Checkpoints, savepoints and Kubernetes HA
metadata use separate subdirectories; this is a shared durable volume, not
an `emptyDir` or a node-local hostPath. Render and inspect it before
enabling:

```sh
helm lint charts/pi-agents -f charts/pi-agents/values-flink-longhorn.yaml
helm template pi-agents charts/pi-agents -n iot-security \
  -f charts/pi-agents/values-flink-longhorn.yaml
```

For a stateful Flink smoke job, add `--set flink.smokeJob=true` in a test
release only. The actual detection job replaces the smoke job later.

The `values-flink-s3.yaml` profile illustrates the future **S3-compatible**
backend (such as RustFS). Set the real endpoint and bucket; create the
referenced Kubernetes Secret with keys `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` outside Git. The template requires all three values
and enables the Flink S3 plugin. Render it with:

```sh
helm lint charts/pi-agents -f charts/pi-agents/values-flink-s3.yaml
helm template pi-agents charts/pi-agents -n iot-security \
  -f charts/pi-agents/values-flink-s3.yaml
```

Changing the checkpoint URI of a running stateful job does not migrate its
state. Before moving from Longhorn to S3, take a verified savepoint, configure
the new storage backend and credentials, then restore from that savepoint and
test restart recovery. Leave the Longhorn state intact until restoration is
confirmed.
