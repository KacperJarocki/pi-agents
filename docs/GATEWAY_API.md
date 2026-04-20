# Gateway API — REST API

> Ostatnia aktualizacja: Kwiecień 2026

---

## Spis treści

1. [Co robi gateway-api?](#co-robi-gateway-api)
2. [Endpointy](#endpointy)
3. [Baza danych](#baza-danych)
4. [Cache](#cache)
5. [WebSocket](#websocket)
6. [Training Router — trening na żądanie](#training-router--trening-na-żądanie)
7. [Proxy do gateway-agent](#proxy-do-gateway-agent)
8. [Konfiguracja](#konfiguracja)
9. [Wdrożenie K8s](#wdrożenie-k8s)

---

## Co robi gateway-api?

Gateway-api to centralne **REST API** systemu. Udostępnia dane z bazy SQLite (urządzenia, anomalie,
metryki, wyniki ML) i pośredniczy w komunikacji z gateway-agent (blokowanie urządzeń, konfiguracja WiFi).

Jest jedynym komponentem, do którego dashboard kieruje żądania — działa jak **fasada** nad całym systemem.

---

## Endpointy

### Core

| Metoda | Ścieżka | Opis |
|--------|---------|------|
| `GET` | `/health` | Health check |
| `GET` | `/metrics` | Metryki Prometheus |
| `GET` | `/api/v1/devices` | Lista urządzeń z risk score |
| `POST` | `/api/v1/devices` | Tworzenie urządzenia |
| `GET` | `/api/v1/devices/{id}` | Szczegóły urządzenia |
| `PATCH` | `/api/v1/devices/{id}` | Aktualizacja urządzenia |
| `GET` | `/api/v1/anomalies` | Nierozwiązane anomalie |
| `POST` | `/api/v1/anomalies` | Tworzenie anomalii |
| `GET` | `/api/v1/anomalies/{id}` | Szczegóły anomalii |
| `PATCH` | `/api/v1/anomalies/{id}/resolve` | Rozwiązanie anomalii |
| `GET` | `/api/v1/alerts` | Zunifikowany feed (anomalie + behavior_alerts) |
| `POST` | `/api/v1/alerts/broadcast` | Broadcast alertu przez WebSocket |
| `WS` | `/ws/alerts` | WebSocket — echo-only |

### Device sub-resources

| Metoda | Ścieżka | Opis |
|--------|---------|------|
| `GET` | `/api/v1/devices/{id}/traffic` | Ostatni ruch sieciowy |
| `GET` | `/api/v1/devices/{id}/destinations` | Docelowe adresy IP |
| `GET` | `/api/v1/devices/{id}/anomalies` | Anomalie urządzenia |
| `GET` | `/api/v1/devices/{id}/inference-history` | Historia wyników ML |
| `GET` | `/api/v1/devices/{id}/behavior-alerts` | Alerty behawioralne |
| `GET` | `/api/v1/devices/{id}/risk-contributors` | Rozkład risk score |
| `GET` | `/api/v1/devices/{id}/behavior-baseline` | Bazowe statystyki zachowania |
| `GET` | `/api/v1/devices/{id}/protocol-signals` | Sygnały na poziomie protokołu |
| `GET` | `/api/v1/devices/{id}/model-config` | Konfiguracja modelu ML per-device |
| `PUT` | `/api/v1/devices/{id}/model-config` | Aktualizacja konfiguracji modelu |
| `GET` | `/api/v1/devices/{id}/model-scores` | Historyczne wyniki modeli |
| `POST` | `/api/v1/devices/{id}/block` | Blokowanie urządzenia |
| `DELETE` | `/api/v1/devices/{id}/block` | Odblokowanie urządzenia |
| `PUT` | `/api/v1/devices/{id}/risk-score` | Manualne nadpisanie risk score |

### Metryki

| Metoda | Ścieżka | Opis |
|--------|---------|------|
| `GET` | `/api/v1/metrics/summary` | Podsumowanie dashboardu |
| `GET` | `/api/v1/metrics/ml-status` | Status gotowości modeli ML |
| `GET` | `/api/v1/metrics/timeline` | Oś czasu ruchu |
| `GET` | `/api/v1/metrics/top-talking` | Najaktywniejsze urządzenia |

### WiFi Gateway (proxy do gateway-agent)

| Metoda | Ścieżka | Opis |
|--------|---------|------|
| `GET` | `/api/v1/gateway/wifi/config` | Aktualna konfiguracja WiFi |
| `PUT` | `/api/v1/gateway/wifi/config` | Zapis konfiguracji WiFi |
| `POST` | `/api/v1/gateway/wifi/validate` | Walidacja konfiguracji |
| `POST` | `/api/v1/gateway/wifi/apply` | Zastosowanie konfiguracji |
| `POST` | `/api/v1/gateway/wifi/rollback` | Rollback konfiguracji |
| `GET` | `/api/v1/gateway/wifi/status` | Status gatewaya |
| `GET` | `/api/v1/gateway/wifi/blocked` | Lista zablokowanych urządzeń |

### ML Pipeline Management

| Metoda | Ścieżka | Opis |
|--------|---------|------|
| `GET` | `/api/v1/ml/config` | Globalna konfiguracja treningu |
| `PUT` | `/api/v1/ml/config` | Aktualizacja globalnej konfiguracji |
| `GET` | `/api/v1/ml/devices/{id}/training-config` | Konfiguracja treningu per-device |
| `PUT` | `/api/v1/ml/devices/{id}/training-config` | Aktualizacja konfiguracji per-device |
| `DELETE` | `/api/v1/ml/devices/{id}/training-config` | Reset do domyślnych |
| `GET` | `/api/v1/ml/devices/{id}/training-data` | Feature buckets urządzenia |
| `GET` | `/api/v1/ml/devices/{id}/raw-flows` | Surowe flow-y urządzenia |
| `POST` | `/api/v1/ml/devices/{id}/train` | Uruchomienie treningu (K8s Job) |
| `GET` | `/api/v1/ml/devices/{id}/train/status` | Status treningu |

---

## Baza danych

### Połączenie

- **Engine**: `sqlite+aiosqlite` (SQLAlchemy async)
- **Tryb WAL**: `PRAGMA journal_mode=WAL` — pozwala na jednoczesne odczyty i zapis
- **Synchronous**: `NORMAL` (szybsze niż FULL, bezpieczne z WAL)
- **busy_timeout**: `5000ms` — czas oczekiwania na blokadę
- **connect_args timeout**: `5.0s`

### Migracje

Przy starcie (`init_db()`) API wykonuje migracje schematu:

1. **`traffic_flows`** — dodaje kolumnę `dns_query TEXT` (jeśli nie istnieje)
2. **`devices`** — dodaje kolumny `last_inference_score REAL`, `last_inference_at TIMESTAMP`
3. **`device_behavior_alerts`** — tworzy tabelę (jeśli nie istnieje)
4. **Indeksy**:
   - `idx_behavior_alert_device_time` na `(device_id, timestamp)`
   - `idx_behavior_alert_device_type_bucket` na `(device_id, alert_type, bucket_start)`

Tabele `global_training_config` i `device_training_config` są tworzone leniwie (lazy) przez
training router przy pierwszym użyciu (`_ensure_tables()`).

---

## Cache

### Architektura

Gateway-api używa własnego **async TTLCache** z:
- **LRU eviction** — najstarsze wpisy usuwane po przekroczeniu `max_size=500`
- **Per-entry TTL** — każdy wpis ma własny czas życia
- **Single-flight** — deduplikacja równoczesnych żądań tego samego klucza (asyncio.Lock + per-key Future)
- **Prefix invalidation** — `invalidate_prefix("device:")` czyści wszystkie wpisy z danym prefixem

### Wartości TTL

| Wzorzec klucza | TTL | Powód |
|----------------|-----|-------|
| `devices:*`, `device:{id}` | 3s | Często odświeżane na dashboardzie |
| `metrics-summary` | 3s | Podsumowanie dashboardu |
| `device-traffic:*` | 5s | Dane historyczne, rzadziej się zmieniają |
| `device-destinations:*` | 5s | — |
| `device-ports:*` | 5s | — |
| `device-dns:*` | 5s | — |
| `device-anomalies:*` | 5s | — |
| `device-history:*` | 5s | — |
| `device-behavior-alerts:*` | 5s | — |
| `device-risk-contributors:*` | 5s | — |
| `device-risk-history:*` | 5s | — |
| `device-protocol-signals:*` | 5s | — |
| `device-model-scores:*` | 5s | — |
| `timeline:*`, `top-talking:*` | 5s | — |
| `ml-status` | 5s | — |
| `device-baseline:*` | 10s | Bazowa statystyka, zmienia się wolno |
| `training-data:*` | 10s | Dane treningowe, rzadko się zmieniają |

### Single-flight

Gdy kilka żądań jednocześnie pyta o ten sam klucz cache:
1. Pierwsze żądanie zaczyna pobieranie z bazy
2. Kolejne żądania czekają na wynik pierwszego (nie uruchamiają kolejnych zapytań DB)
3. Wszystkie otrzymują ten sam wynik

To chroni bazę SQLite przed spike'ami zapytań.

---

## WebSocket

### `/ws/alerts` — echo-only

Endpoint WebSocket jest **echo-only** — odsyła otrzymane wiadomości z metadanymi:

```json
{"type": "echo", "data": "<otrzymana wiadomość>", "timestamp": "..."}
```

### Broadcast

`POST /api/v1/alerts/broadcast` wysyła wiadomość do **wszystkich** podłączonych klientów WebSocket:

```json
{"type": "new_alerts", "count": 3, "data": [...]}
```

**Uwaga**: W obecnej architekturze dashboard **nie** otrzymuje alertów push z gateway-api.
Dashboard ma własny background task (`poll_gateway_alerts()`), który co 30s polluje `GET /api/v1/alerts`
i broadcastuje do swoich klientów WS. Endpoint WS gateway-api jest echo-only.

---

## Training Router — trening na żądanie

### Tworzenie K8s Job

`POST /api/v1/ml/devices/{id}/train` tworzy jednorazowy K8s Job:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: ml-train-device-{id}-{timestamp}
  namespace: iot-security
spec:
  template:
    spec:
      containers:
        - name: ml-trainer
          image: <ML_PIPELINE_IMAGE>
          command: [python, -m, app.train]
          env:
            - name: TRAIN_DEVICE_ID
              value: "{id}"
            # + DATABASE_PATH, MODEL_PATH, inne z konfiguracji
      restartPolicy: Never
  backoffLimit: 0
```

### Status treningu

`GET /api/v1/ml/devices/{id}/train/status` sprawdza:
1. Czy istnieje aktywny Job (`ml-train-device-{id}-*`)
2. Status Joba: `running`, `succeeded`, `failed`, `not_found`

### RBAC

Aby gateway-api mogło tworzyć Jobs, potrzebuje ServiceAccount z uprawnieniami:

- **ServiceAccount**: `gateway-api` (namespace: `iot-security`)
- **Role**: `gateway-api-job-manager`
  - `apiGroups: ["batch"]`, `resources: ["jobs"]`, `verbs: ["create", "get", "list", "delete"]`
- **RoleBinding**: `gateway-api-job-manager` → ServiceAccount `gateway-api`

### Konfiguracja tabel

Tabele `global_training_config` i `device_training_config` są tworzone leniwie przy pierwszym
dostępie do training routera. Globalna konfiguracja jest singleton (id=1), a per-device konfiguracja
jest unikalna na device_id.

---

## Proxy do gateway-agent

Żądania `/api/v1/gateway/wifi/*` i blokowania urządzeń są proxy-owane do gateway-agent:

```
Dashboard → Gateway API → Gateway Agent (port 7000)
           /api/v1/gateway/wifi/apply  →  POST /apply
           /api/v1/devices/{id}/block  →  POST /block
```

Klient HTTP: `httpx.AsyncClient` — tworzony **per-request** (bez connection poola).
Target URL: `GATEWAY_AGENT_URL` (domyślnie `http://gateway-agent.iot-security:7000`).

---

## Konfiguracja

### Zmienne środowiskowe

| Zmienna | Domyślna | Opis |
|---------|----------|------|
| `DATABASE_PATH` | `/data/iot-security.db` | Ścieżka do bazy SQLite |
| `MODEL_PATH` | `/data/models` | Katalog modeli ML (do odczytu metadanych) |
| `LOG_LEVEL` | `info` | Poziom logowania |
| `GATEWAY_AGENT_URL` | `http://gateway-agent.iot-security:7000` | URL gateway-agent |
| `ACTIVE_DEVICE_WINDOW_MINUTES` | `15` | Okno czasowe "aktywnego" urządzenia (minuty) |
| `ML_PIPELINE_IMAGE` | *(z env)* | Obraz Docker do Train Now K8s Jobs |
| `K8S_NAMESPACE` | *(z env)* | Namespace K8s do tworzenia Jobs |

Obsługuje plik `.env` via pydantic-settings (`case_sensitive = False`).

---

## Wdrożenie K8s

### Zasoby

| | CPU | Memory |
|--|-----|--------|
| Requests | 50m | 128Mi |
| Limits | 200m | 256Mi |

### Konfiguracja poda

- **Replicas**: 1
- **serviceAccountName**: `gateway-api` (RBAC dla K8s Jobs)
- **securityContext**: `runAsNonRoot: true`, `runAsUser: 1000`, `fsGroup: 1000`
- **nodeSelector**: `node-role.kubernetes.io/gateway: "true"`
- **Port**: 8080

### Wolumeny

| Wolumen | Typ | Punkt montowania | Opis |
|---------|-----|------------------|------|
| `sqlite-data` | PVC (`iot-security-sqlite`) | `/data` | Wspólna baza SQLite + modele |

### Probes

- **Liveness**: `GET /health:8080`, interwał 30s, initial delay 10s
- **Readiness**: `GET /health:8080`, interwał 10s, initial delay 5s

### PodDisruptionBudget

`minAvailable: 1` — gwarantuje dostępność API podczas rolling update.

### Ingress

| Host | TLS Secret |
|------|------------|
| `iot-api.homelab.kacperjarocki.dev` | Certificate (cert-manager, Let's Encrypt) |

---

## Powiązane dokumenty

- [Dashboard](DASHBOARD.md) — jak dashboard komunikuje się z API
- [ML Pipeline](ML_PIPELINE.md) — jak dane ML trafiają do bazy
- [Gateway Agent](GATEWAY_AGENT.md) — endpointy proxy-owane przez API
- [Infrastructure](INFRASTRUCTURE.md) — RBAC, Ingress, PVC
