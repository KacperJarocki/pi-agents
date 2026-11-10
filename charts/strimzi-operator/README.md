# Strimzi Operator Chart

This chart wraps the pinned Strimzi OCI chart as a dependency. It is deployed
as a separate Argo CD Application (`k8s/argocd/strimzi-operator.yaml`, sync
wave 0) so the Kafka custom resources can depend on the operator and its CRDs.

Build the dependency locally:

```sh
helm dependency build charts/strimzi-operator
helm lint charts/strimzi-operator
```

The generated dependency archive is ignored. `Chart.lock` is committed so CI
and Argo CD use the pinned dependency digest.
