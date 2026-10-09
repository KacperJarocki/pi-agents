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

The Kafka profile uses three KRaft dual-role nodes (replication factor 3,
`min.insync.replicas=2`), local-path storage, a 2GiB volume per node, a
768MiB heap in a 1.5GiB container, and a required node affinity that keeps
brokers off nodes labelled `node-role.kubernetes.io/gateway=true`.

`kafka.topics` creates one `KafkaTopic` per v2 topic (`traffic.flows.v2`,
`traffic.features.v2`, `traffic.detections.v2`, `traffic.health.v2`,
`traffic.late.v2`) with its partition count and time-based retention. Size the
volume for that retention before enabling the profile on real traffic; the
2GiB default only fits short tests. Changing the partition count of
`traffic.flows.v2` requires a replay plan. Do not enable it while the node-local storage path or Longhorn is
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

The default checkpoint backend is **S3 on the in-chart RustFS**
(`values-flink-s3.yaml` enables both). RustFS runs as one pod on a
`local-path` volume off the gateway node; a post-install/post-upgrade Job
creates the buckets listed in `rustfs.buckets`. Create the credentials Secret
outside Git before enabling it; RustFS, the bucket Job and Flink all read it:

```sh
kubectl -n iot-security create secret generic rustfs-credentials \
  --from-literal=AWS_ACCESS_KEY_ID=<access-key> \
  --from-literal=AWS_SECRET_ACCESS_KEY=<secret-key>
```

With an empty `flink.checkpoint.s3.endpoint` Flink uses
`http://<release>-rustfs:9000`; set it to point at an external S3 store
instead. The sizing is 2 TaskManagers × 2 slots (2 GiB each) and a 1.5 GiB
JobManager, enough for a parallelism-4 job.

```sh
helm lint charts/pi-agents -f charts/pi-agents/values-flink-s3.yaml
helm template pi-agents charts/pi-agents -n iot-security \
  -f charts/pi-agents/values-flink-s3.yaml
```

The **Longhorn RWX** profile (`values-flink-longhorn.yaml`) mounts a shared
claim at `/flink-state` in both pod roles instead. Checkpoints, savepoints and
Kubernetes HA metadata use separate subdirectories in either backend.

For a stateful Flink smoke job, add `--set flink.smokeJob=true` in a test
release only. The actual detection job replaces the smoke job later.

Changing the checkpoint URI of a running stateful job does not migrate its
state. Before moving from Longhorn to S3, take a verified savepoint, configure
the new storage backend and credentials, then restore from that savepoint and
test restart recovery. Leave the Longhorn state intact until restoration is
confirmed.

## PostgreSQL (CloudNativePG)

`postgres.enabled` creates a two-instance CloudNativePG `Cluster`
(`<release>-pg`, database and owner `incidents`) on `local-path` volumes with a
2 GiB limit per instance, required anti-affinity across nodes, and no
instance on the gateway node. WAL archiving and a daily `ScheduledBackup` go to
`s3://pi-agents/postgres` on RustFS through the Barman Cloud plugin
(`ObjectStore` `<release>-pg-backups`, 14-day retention), using the same
`rustfs-credentials` Secret as Flink.

The CNPG operator (chart 0.29.1) and the Barman Cloud plugin (chart 0.8.0) are
installed by Flux from `k8s/flux/cnpg-operator-helmrelease.yaml` into
`cnpg-system`; the plugin needs cert-manager. The `pi-agents` HelmRelease
waits for the plugin before installing.

```sh
helm lint charts/pi-agents -f charts/pi-agents/values-postgres.yaml
helm template pi-agents charts/pi-agents -n iot-security \
  -f charts/pi-agents/values-postgres.yaml
```
