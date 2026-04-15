# k8s

Katalog `k8s/` zawiera manifesty deploymentu systemu.

## Struktura

- `base/` - namespace i PVC
- `gateway/` - workloady systemu
- `overlays/gateway-prod/` - overlay włączający realne apply Wi-Fi

## Tryby wdrożenia

### Safe mode
```bash
kubectl apply -k k8s/base
kubectl apply -k k8s/gateway
```

### Gateway prod
```bash
kubectl apply -k k8s/overlays/gateway-prod
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
