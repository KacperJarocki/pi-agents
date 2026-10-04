# Strimzi Operator Chart

This chart wraps the pinned Strimzi OCI chart as a dependency. It is deployed
as a separate Flux `HelmRelease` so the Kafka custom resources can explicitly
depend on the operator and its CRDs.

Build the dependency locally:

```sh
helm dependency build charts/strimzi-operator
helm lint charts/strimzi-operator
```

The generated dependency archive is ignored. `Chart.lock` is committed so CI
and Flux use the pinned dependency digest.
