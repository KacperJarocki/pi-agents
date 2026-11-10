# k8s

Katalog `k8s/` zawiera manifesty deploymentu systemu.

## Struktura

- `argocd/` - Argo CD: Applications operatorów (CNPG, Barman, Flink, Strimzi), `gateway-agent` oraz ApplicationSet `pi-agents` (dev + prod)
- `namespaces/` - namespace `iot-security-dev` (Application `pi-agents-namespaces`, fala -1, bo Strimzi tworzy w nim RoleBindingi zanim powstanie dev)
- `envs/` - wartości per środowisko: `base.yaml` (wspólne), `<env>/values.yaml` (rozmiary), `<env>/images.yaml` (tagi obrazów, promowane)
- `values/` - Helm values Applications operatorów
- `base/` - namespace i ConfigMap streamingu
- `gateway/` - workloady systemu
- `overlays/gateway-prod/` - overlay włączający realne apply Wi-Fi

Katalog `argocd/` jest synchronizowany przez Argo CD z repo homelab (nested app-of-apps,
AppProject `pi-agents`). Dev (`iot-security-dev`) dostaje każdy build obrazów z `main`
automatycznie; prod (`iot-security`) zmienia się tylko przez PR z workflow
"Promote to prod". Application `gateway-agent` wskazuje na `gateway/` (safe mode);
żeby włączyć realne apply, przestaw `path` na `overlays/gateway-prod`.

## Tryby wdrożenia

Ręczne `kubectl apply -k` tylko do testów; Argo CD z `selfHeal` cofnie zmiany
niezgodne z Gitem.

### Safe mode
```bash
kubectl kustomize k8s/gateway
```

### Gateway prod
```bash
kubectl kustomize k8s/overlays/gateway-prod
```

Overlay `gateway-prod` włącza realne sterowanie AP (`ENABLE_APPLY=true`).

## Privileged workloads

Uprzywilejowane workloady MVP:

- `gateway-agent`
- `collector`

## Co sprawdzać po deployu

1. `kubectl get pods -n iot-security`
2. `gateway-agent /status`
3. `gateway-api /health`
4. `devices`, `summary`, `ml-status`

## Troubleshooting

- jeśli SSID nie działa, sprawdź `gateway-agent`
- jeśli brak ruchu, sprawdź `collector`
- jeśli brak modeli, sprawdź `ml-trainer` i `ml-inference`
