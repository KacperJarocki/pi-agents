# Infrastructure — K8s + CI/CD + Local Dev

> Ostatnia aktualizacja: Kwiecień 2026

---

## Spis treści

1. [Architektura klastra](#architektura-klastra)
2. [Kustomize — hierarchia manifestów](#kustomize--hierarchia-manifestów)
3. [PVC — persistent storage](#pvc--persistent-storage)
4. [Limity zasobów](#limity-zasobów)
5. [Node affinity](#node-affinity)
6. [RBAC](#rbac)
7. [Ingress i TLS](#ingress-i-tls)
8. [PodDisruptionBudgets](#poddisruptionbudgets)
9. [CI/CD — GitHub Actions](#cicd--github-actions)
10. [Local dev — docker-compose](#local-dev--docker-compose)
11. [Diagram wolumenów](#diagram-wolumenów)

---

## Architektura klastra

- **3 mastery + 2 workery** (K3s)
- **worker-1** = gateway node (karta WiFi, WiFi AP)
- **Traefik** — ingress controller
- **cert-manager** — TLS z Let's Encrypt (Cloudflare DNS-01)
- **Longhorn** — distributed storage
- **Alloy** — zbieranie metryk

---

## Kustomize — hierarchia manifestów

```
k8s/
├── base/                          # Bazowe zasoby
│   ├── kustomization.yaml         # namespace + PVC
│   ├── namespace.yaml             # Namespace: iot-security
│   └── pvc.yaml                   # PVC: iot-security-sqlite (5Gi Longhorn)
│
├── gateway/                       # Warstwa workloadów
│   ├── kustomization.yaml         # bases: ../base + wszystkie manifesty
│   ├── collector-deployment.yaml
│   ├── gateway-agent-deployment.yaml
│   ├── gateway-agent-service.yaml
│   ├── gateway-api-deployment.yaml
│   ├── gateway-api-service.yaml
│   ├── gateway-api-ingress.yaml
│   ├── gateway-api-certificate.yaml
│   ├── ml-inference-deployment.yaml
│   ├── ml-trainer-cronjob.yaml
│   ├── ml-train-sa.yaml           # ServiceAccount: gateway-api
│   ├── ml-train-rbac.yaml         # Role + RoleBinding (Jobs)
│   ├── dashboard-deployment.yaml
│   ├── dashboard-service.yaml
│   ├── dashboard-ingress.yaml
│   ├── dashboard-certificate.yaml
│   └── pdb.yaml                   # PodDisruptionBudgets
│
└── overlays/
    └── gateway-prod/              # Overlay produkcyjny
        ├── kustomization.yaml     # bases: ../../gateway + patch
        └── gateway-agent-enable-apply.yaml  # ENABLE_APPLY=true
```

### Użycie

```bash
# Dry-run (walidacja)
kubectl kustomize k8s/gateway

# Deploy produkcyjny
kubectl apply -k k8s/overlays/gateway-prod
```

---

## PVC — persistent storage

### iot-security-sqlite

| Parametr | Wartość |
|----------|--------|
| Nazwa | `iot-security-sqlite` |
| Rozmiar | 5Gi |
| Access mode | ReadWriteOnce (RWO) |
| Storage class | `longhorn` |
| Współdzielone przez | collector, gateway-api, ml-inference, ml-trainer |
| Punkt montowania | `/data` |

**Zawartość PVC**:
- `/data/iot-security.db` — baza SQLite (WAL mode)
- `/data/models/` — pliki modeli ML (joblib)

**Uwaga**: RWO oznacza, że PVC może być zamontowany na jednym węźle jednocześnie.
To działa, bo wszystkie pody korzystające z PVC mają `nodeSelector: gateway=true`
i działają na tym samym węźle.

### gateway-agent — hostPath (nie PVC)

Gateway-agent **nie** używa PVC. Używa `hostPath: /var/lib/gateway-agent` (DirectoryOrCreate),
bo potrzebuje plików persystentnych na poziomie hosta (leases, configs hostapd/dnsmasq).

---

## Limity zasobów

### Podsumowanie per workload

| Workload | Typ | CPU req | CPU lim | Mem req | Mem lim |
|----------|-----|---------|---------|---------|---------|
| collector | Deployment | 150m | 500m | 192Mi | 384Mi |
| gateway-agent | Deployment | 20m | 200m | 64Mi | 256Mi |
| gateway-api | Deployment | 50m | 200m | 128Mi | 256Mi |
| ml-inference | Deployment | 300m | 500m | 512Mi | 1024Mi |
| ml-trainer | CronJob | 100m | 500m | 256Mi | 512Mi |
| dashboard | Deployment | 50m | 100m | 64Mi | 128Mi |

### Łączne zasoby (Deployments, bez CronJob)

| | CPU requests | CPU limits | Mem requests | Mem limits |
|--|-------------|-----------|-------------|-----------|
| **Suma** | 570m | 1500m | 960Mi | 2048Mi |
| + CronJob (gdy aktywny) | 670m | 2000m | 1216Mi | 2560Mi |

### Dlaczego takie wartości?

- **ml-inference** ma największe limity, bo ładuje modele ML do pamięci i wykonuje scoring
- **collector** potrzebuje CPU na tshark (parsowanie pcap)
- **gateway-agent** i **dashboard** mają minimalne wymagania
- **ml-trainer** uruchamia się co 30 minut i potrzebuje burst CPU do treningu

---

## Node affinity

| Workload | nodeSelector |
|----------|-------------|
| collector | `node-role.kubernetes.io/gateway: "true"` |
| gateway-agent | `node-role.kubernetes.io/gateway: "true"` |
| gateway-api | `node-role.kubernetes.io/gateway: "true"` |
| ml-inference | `node-role.kubernetes.io/gateway: "true"` |
| ml-trainer | `node-role.kubernetes.io/gateway: "true"` |
| dashboard | *(brak — dowolny węzeł)* |

Wszystkie workloady **oprócz dashboard** muszą działać na węźle gateway, bo:
- collector i gateway-agent potrzebują fizycznego interfejsu WiFi (`hostNetwork`)
- gateway-api i ml-* potrzebują PVC (RWO, zamontowany na jednym węźle)

### Labelowanie węzła

```bash
kubectl label node <worker-1-name> node-role.kubernetes.io/gateway=true
```

---

## RBAC

System definiuje jeden zestaw RBAC:

### ServiceAccount: `gateway-api`

Używany przez pod `gateway-api` do tworzenia K8s Jobs (Train Now).

### Role: `gateway-api-job-manager`

```yaml
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "list", "delete"]
```

### RoleBinding: `gateway-api-job-manager`

Wiąże Role z ServiceAccount `gateway-api` w namespace `iot-security`.

**Uwaga**: Nie ma NetworkPolicies — cała komunikacja między podami jest niezabezpieczona
(domyślna polityka K8s = allow all). Dopuszczalne w środowisku homelab,
ale do rozważenia przy produkcji.

---

## Ingress i TLS

### Ingress Controller: Traefik

Traefik jest domyślnym ingress controllerem w K3s. Manifesty używają standardowego
`Ingress` z `ingressClassName: traefik`.

### Endpointy

| Serwis | Host | TLS |
|--------|------|-----|
| gateway-api | `iot-api.homelab.kacperjarocki.dev` | Certificate (cert-manager) |
| dashboard | `iot-dashboard.homelab.kacperjarocki.dev` | Certificate (cert-manager) |

### cert-manager

- **Issuer**: `letsencrypt-http-prod`
- **Challenge**: Cloudflare DNS-01
- **Certyfikaty**: osobny `Certificate` resource dla każdego serwisu

---

## PodDisruptionBudgets

| PDB | Workload | minAvailable |
|-----|----------|-------------|
| `gateway-api-pdb` | gateway-api | 1 |
| `dashboard-pdb` | dashboard | 1 |

PDB gwarantują, że podczas voluntary disruption (np. drain node, rolling update)
przynajmniej 1 pod danego workloadu jest dostępny.

---

## CI/CD — GitHub Actions

### docker-build.yml

**Trigger**: Push na `main` (ścieżki `images/**`), PR na `images/**`, manual dispatch.

**Matrix** (5 obrazów):

| Katalog | Obraz | Registry |
|---------|-------|----------|
| `images/gateway-api` | `ghcr.io/kacperjarocki/gateway-api` | GHCR |
| `images/collector` | `ghcr.io/kacperjarocki/collector` | GHCR |
| `images/gateway-agent` | `ghcr.io/kacperjarocki/gateway-agent` | GHCR |
| `images/ml-pipeline` | `ghcr.io/kacperjarocki/ml-pipeline` | GHCR |
| `images/dashboard` | `ghcr.io/kacperjarocki/dashboard` | GHCR |

**Platformy**: `linux/amd64`, `linux/arm64` (multi-arch).

**Tagi**:
- `latest` — zawsze na `main`
- `sha-<8 znaków>` — commit SHA

**PR builds**: tylko `amd64`, bez push (walidacja buildu).

### validate.yml

**Trigger**: Push na `main`, wszystkie PR.

**Job 1: python** — Python 3.12:
1. `python -m compileall images` — sprawdzenie składni
2. Instalacja requirements (`gateway-agent` + `ml-pipeline`)
3. `python -m unittest discover` — testy jednostkowe

**Job 2: k8s** — Kind cluster:
1. Instalacja kubectl v1.30
2. Instalacja cert-manager CRDs (potrzebne do walidacji Certificate resources)
3. `kubectl kustomize k8s/gateway` — dry-run Kustomize
4. Dry-run apply

---

## Local dev — docker-compose

```bash
docker-compose up --build

# Serwisy:
# - Dashboard:  http://localhost:3000
# - API:        http://localhost:8080
# - API Docs:   http://localhost:8080/docs
```

### Serwisy

| Serwis | Porty | Network | Profile | Uwagi |
|--------|-------|---------|---------|-------|
| `gateway-api` | 8080:8080 | default | *(domyślny)* | — |
| `collector` | — | host | `gateway` | Wymaga `docker-compose --profile gateway` |
| `ml-trainer` | — | default | *(domyślny)* | restart: on-failure |
| `ml-inference` | — | default | *(domyślny)* | restart: unless-stopped, depends: ml-trainer |
| `dashboard` | 3000:8080 | default | *(domyślny)* | depends: gateway-api |
| `gateway-agent` | — | host | `gateway` | privileged, `ENABLE_APPLY=false` |

**Profile `gateway`**: collector i gateway-agent wymagają dostępu do interfejsu WiFi,
więc są w profilu `gateway` — nie uruchamiają się domyślnie.

### Wolumeny

| Wolumen | Współdzielone przez |
|---------|---------------------|
| `sqlite-data` | gateway-api, collector, ml-trainer, ml-inference |
| `model-data` | ml-trainer, ml-inference, gateway-api |
| `gateway-agent-state` | gateway-agent, collector |

---

## Diagram wolumenów

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   collector   │     │  gateway-api  │     │ ml-inference  │
│              │     │              │     │              │
│  /data ──────┼─────┼── /data ─────┼─────┼── /data      │
│              │     │              │     │              │
│  /gateway-   │     │              │     │              │
│   state ─────┼──┐  │              │     │              │
└──────────────┘  │  └──────────────┘     └──────────────┘
                  │         │                    │
                  │   PVC: iot-security-sqlite   │
                  │   (5Gi Longhorn RWO)         │
                  │         │                    │
                  │  ┌──────────────┐            │
                  │  │  ml-trainer   │            │
                  │  │  (CronJob)    ├────────────┘
                  │  │  /data        │
                  │  └──────────────┘
                  │
                  │  ┌──────────────┐
                  └──┤gateway-agent │
                     │              │
                     │ /data        │ ← hostPath: /var/lib/gateway-agent
                     └──────────────┘

   /data/iot-security.db    ← SQLite database (WAL mode)
   /data/models/            ← ML models (joblib files)
```

---

## Powiązane dokumenty

- [Collector](COLLECTOR.md) — wymagania K8s collectora
- [Gateway Agent](GATEWAY_AGENT.md) — hostPath, hostNetwork, privileged
- [Gateway API](GATEWAY_API.md) — RBAC, ServiceAccount
- [Dashboard](DASHBOARD.md) — Ingress, brak nodeSelector
- [ML Pipeline](ML_PIPELINE.md) — CronJob, Deployment ml-inference
- [Data Flow](DATA_FLOW.md) — jak dane przepływają między komponentami
