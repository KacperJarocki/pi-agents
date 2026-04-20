# Dashboard — Web UI

> Ostatnia aktualizacja: Kwiecień 2026

---

## Spis treści

1. [Co robi dashboard?](#co-robi-dashboard)
2. [Architektura proxy](#architektura-proxy)
3. [Widoki](#widoki)
4. [System alertów WebSocket](#system-alertów-websocket)
5. [Device detail — funkcje JavaScript](#device-detail--funkcje-javascript)
6. [Real-time updates](#real-time-updates)
7. [Tech stack](#tech-stack)
8. [Konfiguracja](#konfiguracja)
9. [Wdrożenie K8s](#wdrożenie-k8s)

---

## Co robi dashboard?

Dashboard to **interfejs użytkownika** systemu — wyświetla dane z gateway-api w formie
interaktywnych widoków. Jest to aplikacja server-side (FastAPI + Jinja2) wzbogacona o
fragmenty klienckie (HTMX + Alpine.js + Chart.js).

Dashboard **nie** ma bezpośredniego dostępu do bazy danych — wszystkie dane pobiera przez
REST API gateway-api.

---

## Architektura proxy

Dashboard działa jako **reverse proxy** — żądania z frontendu `/api/*` są przekierowywane
do gateway-api:

```
Przeglądarka   →   Dashboard (port 8080)   →   Gateway API (port 8080)
  /api/devices  →   fetch_api("/devices")   →   GET /api/v1/devices
```

### Mapowanie tras

| Dashboard route | Metoda | Gateway API endpoint |
|-----------------|--------|----------------------|
| `/api/devices` | GET | `/api/v1/devices` |
| `/api/devices/{id}` | GET | `/api/v1/devices/{id}` |
| `/api/devices/{id}/traffic` | GET | `/api/v1/devices/{id}/traffic` |
| `/api/devices/{id}/destinations` | GET | `/api/v1/devices/{id}/destinations` |
| `/api/devices/{id}/anomalies` | GET | `/api/v1/devices/{id}/anomalies` |
| `/api/devices/{id}/inference-history` | GET | `/api/v1/devices/{id}/inference-history` |
| `/api/devices/{id}/behavior-alerts` | GET | `/api/v1/devices/{id}/behavior-alerts` |
| `/api/devices/{id}/risk-contributors` | GET | `/api/v1/devices/{id}/risk-contributors` |
| `/api/devices/{id}/behavior-baseline` | GET | `/api/v1/devices/{id}/behavior-baseline` |
| `/api/devices/{id}/protocol-signals` | GET | `/api/v1/devices/{id}/protocol-signals` |
| `/api/devices/{id}/model-config` | GET/PUT | `/api/v1/devices/{id}/model-config` |
| `/api/devices/{id}/model-scores` | GET | `/api/v1/devices/{id}/model-scores` |
| `/api/devices/{id}/block` | POST/DELETE | `/api/v1/devices/{id}/block` |
| `/api/blocked` | GET | `/api/v1/gateway/wifi/blocked` |
| `/api/alerts` | GET | `/api/v1/alerts` |
| `/api/anomalies` | GET | `/api/v1/anomalies` |
| `/api/metrics/summary` | GET | `/api/v1/metrics/summary` |
| `/api/metrics/timeline` | GET | `/api/v1/metrics/timeline` |
| `/api/metrics/top-talking` | GET | `/api/v1/metrics/top-talking` |
| `/api/metrics/ml-status` | GET | `/api/v1/metrics/ml-status` |
| `/api/ml/config` | GET/PUT | `/api/v1/ml/config` |
| `/api/ml/devices/{id}/training-config` | GET/PUT/DELETE | `/api/v1/ml/devices/{id}/training-config` |
| `/api/ml/devices/{id}/training-data` | GET | `/api/v1/ml/devices/{id}/training-data` |
| `/api/ml/devices/{id}/raw-flows` | GET | `/api/v1/ml/devices/{id}/raw-flows` |
| `/api/ml/devices/{id}/train` | POST | `/api/v1/ml/devices/{id}/train` |
| `/api/ml/devices/{id}/train/status` | GET | `/api/v1/ml/devices/{id}/train/status` |

### Gateway form handlers (nie-proxy)

| Route | Metoda | Akcja |
|-------|--------|-------|
| `/gateway/validate` | POST | Walidacja konfiguracji WiFi |
| `/gateway/save` | POST | Zapis konfiguracji WiFi |
| `/gateway/apply` | POST | Zastosowanie konfiguracji WiFi |
| `/gateway/rollback` | POST | Rollback konfiguracji WiFi |

### HTMX partial routes

| Route | Opis |
|-------|------|
| `/partial/devices` | Fragment HTML z listą urządzeń |
| `/partial/anomalies` | Fragment z anomaliami |
| `/partial/timeline` | Fragment z osią czasu |
| `/partial/top-talkers` | Fragment z top talkers |
| `/partial/alerts` | Fragment z alertami |

### Klient HTTP

- **Biblioteka**: `httpx.AsyncClient` (singleton, tworzony przy starcie)
- **Timeouts**: connect=4s, read=8s, write=8s, pool=8s
- **Base URL**: `GATEWAY_API_URL` (domyślnie `http://gateway-api.iot-security:8080`)

---

## Widoki

### 1. Index (`/`)

Główna strona dashboardu z przeglądem systemu:

- **Karty metryk** — łączna liczba urządzeń, aktywne, anomalie, średni risk score
- **Feed alertów** — ostatnie alerty (anomalie + behavior_alerts), auto-odświeżane
- **Oś czasu** — wykres ruchu sieciowego (Chart.js)
- **Top talkers** — najaktywniejsze urządzenia
- **Siatka urządzeń** — kafelki ze statusem każdego urządzenia (tabs: all/active/at-risk)

HTMX odświeża poszczególne sekcje co `REFRESH_INTERVAL` sekund (domyślnie 30s).

### 2. Device Detail (`/devices/{id}`)

Szczegółowy widok pojedynczego urządzenia z **15+ sekcjami**:

| Sekcja | Dane | Odświeżanie |
|--------|------|-------------|
| Nagłówek | MAC, IP, hostname, risk score, status | 5s (loadDevice) |
| Risk Contributors | Rozkład: ML, behavior, protocol, correlation | 5s |
| Behavior Alerts | Lista alertów behawioralnych | 5s |
| Traffic | Ostatni ruch sieciowy | 5s |
| Destinations | Docelowe adresy IP | 5s |
| Anomalies | Lista anomalii | 5s |
| Inference History | Wykres historii ML scoring | 5s |
| Protocol Signals | Sygnały DNS/ICMP | 5s |
| Behavior Baseline | Bazowe statystyki zachowania | 5s |
| Multi-Model Timeline | Porównanie wyników różnych modeli (Chart.js) | 5s |
| ML Health | Status modelu, konfiguracja, metryki | 60s |
| Model Config | Konfiguracja modelu per-device | 60s |
| Training Config | Parametry treningu per-device | 60s |
| Training Data | Feature buckets (tabela) | 60s |
| Raw Flows | Surowe flow-y (paginowane) | ręczne |
| Block/Unblock | Przycisk blokowania urządzenia | ręczne |
| Train Now | Przycisk uruchomienia treningu | ręczne |

### 3. Gateway Settings (`/gateway`)

Formularz konfiguracji WiFi AP:

- Pola: SSID, hasło, kanał, kod kraju, interfejsy, podsieć, zakres DHCP
- Przyciski: Validate, Save, Apply, Rollback
- Status aktualnej konfiguracji (ładowany z API)

---

## System alertów WebSocket

Dashboard ma własny system push-alertów, niezależny od WS gateway-api:

```
┌─────────────────────────────────────────────────────────────┐
│  Dashboard (serwer)                                         │
│                                                             │
│  poll_gateway_alerts()  ──── co 30s ────►  GET /api/v1/alerts
│       │                                      (gateway-api)  │
│       │ nowe alerty (dedup via seen_keys)                    │
│       ▼                                                     │
│  broadcast do WS klientów                                   │
│       │                                                     │
│       │  {"type": "new_alerts", "count": N, "data": [...]}  │
│       ▼                                                     │
│  Przeglądarka                                               │
│       │                                                     │
│  Alpine.js $store.ws                                        │
│       │  exponential backoff (1s → 30s)                     │
│       ▼                                                     │
│  Toast notifications                                        │
│       │  auto-dismiss po 10s                                │
└─────────────────────────────────────────────────────────────┘
```

### Deduplikacja

- `seen_keys` — zbiór (set) kluczy `"{source}:{id}"` (np. `"anomaly:42"`)
- Alerty z kluczami już w `seen_keys` są pomijane
- Gdy `seen_keys` przekroczy 1000 wpisów, jest czyszczony (reset)
- **Znany problem**: po restarcie dashboardu `seen_keys` jest pusty → mogą pojawić się duplikaty toastów

### Klient WS (Alpine.js)

```javascript
// base.html — Alpine.js store
Alpine.store('ws', {
    socket: null,
    reconnectDelay: 1000,  // start: 1s
    maxDelay: 30000,        // max: 30s
    connect() { /* WebSocket z exponential backoff */ },
    onMessage(data) { /* toast notification */ }
})
```

Toast notyfikacje:
- Wyświetlane w prawym górnym rogu
- Auto-dismiss po 10 sekundach
- Kolor zależny od severity (warning = żółty, critical = czerwony)

---

## Device detail — funkcje JavaScript

Widok device detail zawiera rozbudowaną logikę JavaScript do odświeżania danych:

| Funkcja | Interwał | Opis |
|---------|----------|------|
| `loadDevice()` | 5s | Cykl heavy/light — ładuje pełne dane lub tylko risk score |
| `loadModelConfig()` | 60s | Konfiguracja modelu ML |
| `loadMlHealth()` | 60s | Status zdrowia modelu |
| `loadTrainingData()` | 60s | Feature buckets (tabela) |
| `loadTrainingConfig()` | 60s | Parametry treningu |
| `trainNow()` | ręczne | Uruchomienie treningu (K8s Job) |
| `pollTrainStatus()` | 5s | Polling statusu treningu (po trainNow) |
| `changeModel()` | ręczne | Zmiana typu modelu |
| `toggleBlock()` | ręczne | Blokowanie/odblokowanie urządzenia |
| `renderBars()` | przy loadDevice | Renderowanie wykresów risk contributors |
| `loadMultiModelTimeline()` | przy loadDevice | Wykres porównania modeli (Chart.js) |
| `loadRawFlows()` | ręczne | Surowe flow-y (paginowane, przycisk "Load more") |

### Cykl heavy/light

`loadDevice()` alternatywnie ładuje "heavy" (wszystkie sekcje) i "light" (tylko nagłówek + risk).
To zmniejsza obciążenie API — pełne odświeżenie co 10s zamiast co 5s, a risk score odświeżany co 5s.

---

## Real-time updates

| Mechanizm | Interwał | Cel |
|-----------|----------|-----|
| HTMX polling | 30s | Sekcje na stronie głównej (devices, anomalies, timeline, top-talkers, alerts) |
| JS setInterval | 5s | Device detail: dane urządzenia, risk score |
| JS setInterval | 60s | Device detail: ML health, model config, training data |
| WS push | ~30s | Toast alertów (nowe anomalie, behavior alerts) |
| Ręczne | — | Train Now, Block/Unblock, Raw Flows, Change Model |

---

## Tech stack

| Technologia | Wersja | Rola | Źródło |
|-------------|--------|------|--------|
| FastAPI | — | Backend, routing, proxy | pip |
| Jinja2 | — | Szablony HTML | pip |
| HTMX | 1.9.10 | Partial updates, AJAX | CDN |
| Alpine.js | 3.14.8 | Reaktywność kliencka, WS store | CDN |
| TailwindCSS + DaisyUI | — | Style CSS | Pre-built `dist.css` |
| Chart.js | — | Wykresy (timeline, model scores) | Self-hosted |
| httpx | — | Async HTTP client | pip |

---

## Konfiguracja

### Zmienne środowiskowe

| Zmienna | Domyślna | Opis |
|---------|----------|------|
| `GATEWAY_API_URL` | `http://gateway-api.iot-security:8080` | URL gateway-api |
| `REFRESH_INTERVAL` | `30` | Interwał odświeżania HTMX (sekundy) |

---

## Wdrożenie K8s

### Zasoby

| | CPU | Memory |
|--|-----|--------|
| Requests | 50m | 64Mi |
| Limits | 100m | 128Mi |

### Konfiguracja poda

- **Replicas**: 1
- **securityContext**: `runAsNonRoot: true`, `runAsUser: 1000`
- **Port**: 8080
- **Brak nodeSelector** — dashboard może działać na dowolnym węźle
- **Brak wolumenów** — nie potrzebuje dostępu do bazy ani modeli

### Probes

- **Liveness**: `GET /health:8080`, interwał 30s, initial delay 10s
- **Readiness**: `GET /health:8080`, interwał 10s, initial delay 5s

### PodDisruptionBudget

`minAvailable: 1` — gwarantuje dostępność dashboardu podczas rolling update.

### Ingress

| Host | TLS Secret |
|------|------------|
| `iot-dashboard.homelab.kacperjarocki.dev` | Certificate (cert-manager, Let's Encrypt) |

---

## Powiązane dokumenty

- [Gateway API](GATEWAY_API.md) — endpointy, z których dashboard pobiera dane
- [ML Pipeline](ML_PIPELINE.md) — jak powstają dane ML wyświetlane na dashboardzie
- [Infrastructure](INFRASTRUCTURE.md) — Ingress, TLS, K8s deployment
