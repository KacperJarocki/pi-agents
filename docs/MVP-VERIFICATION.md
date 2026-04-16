# MVP Verification

Ta checklista służy do potwierdzenia, że MVP działa end-to-end.

## 1. Gateway / Wi-Fi

Sprawdź:

```bash
curl -s http://10.20.20.6:7000/status
```

Oczekiwane:

- `hostapd.running=true`
- `dnsmasq.running=true`
- `lease_count >= 1`

## 2. Devices / Presence

```bash
curl -sS https://iot-api.homelab.kacperjarocki.dev/api/v1/devices
curl -sS https://iot-api.homelab.kacperjarocki.dev/api/v1/metrics/summary
```

Oczekiwane:

- connected devices są widoczne
- `active_devices` zgadza się z liczbą klientów DHCP

## 3. Collector / Traffic

```bash
kubectl logs -n iot-security deploy/collector --since=15m
curl -sS https://iot-api.homelab.kacperjarocki.dev/api/v1/metrics/summary
curl -sS 'https://iot-api.homelab.kacperjarocki.dev/api/v1/metrics/timeline?hours=24'
```

Oczekiwane:

- `buffer_flushed`
- `total_traffic_mb > 0`
- timeline nie jest puste

## 4. ML Training

```bash
kubectl get jobs -n iot-security
kubectl logs -n iot-security job/<ml-trainer-job>
curl -sS https://iot-api.homelab.kacperjarocki.dev/api/v1/metrics/ml-status
```

Oczekiwane:

- `training_complete_for_device`
- `device_models_ready > 0`

## 5. ML Inference / Anomalies

```bash
kubectl logs -n iot-security deploy/ml-inference --since=30m
curl -sS https://iot-api.homelab.kacperjarocki.dev/api/v1/anomalies
curl -sS https://iot-api.homelab.kacperjarocki.dev/api/v1/devices
```

Oczekiwane:

- `model_loaded`
- `inference_complete`
- anomaly lub wzrost `risk_score` po nietypowym ruchu

## 6. Device Console / Explainability

```bash
curl -sS https://iot-api.homelab.kacperjarocki.dev/api/v1/devices/5/behavior-alerts?limit=10\&since_hours=168
curl -sS https://iot-api.homelab.kacperjarocki.dev/api/v1/devices/5/risk-contributors
curl -sS https://iot-api.homelab.kacperjarocki.dev/api/v1/devices/5/behavior-baseline?days=7
curl -sS https://iot-api.homelab.kacperjarocki.dev/api/v1/devices/5/protocol-signals?hours=24
```

Oczekiwane:

- endpointy zwracają poprawny `device_id`
- `behavior-alerts` pokazuje heurystyki aktywowane dla urządzenia
- `risk-contributors` zawiera `ml_risk`, `behavior_risk`, `protocol_risk`, `correlation_bonus`, `risk_delta` i `top_reason`
- `risk-contributors` deduplikuje podobne alerty po `alert_type` i pokazuje `effective_score` po decay czasowym
- `behavior-baseline` pokazuje medianę i `p95` dla ostatnich 7 dni
- `protocol-signals` zwraca DNS failure i ICMP echo summary

## 7. Dashboard / Device Page

```bash
curl -sS https://iot-dashboard.homelab.kacperjarocki.dev/devices/5
```

Oczekiwane:

- strona renderuje sekcje `Behavior Alerts`, `Risk Contributors`, `Behavior Baseline`, `Protocol Signals`
- strona renderuje też sekcję `Risk Breakdown` oraz status `rising|stable|cooling_down`
- wykres `Inference Trail` pokazuje próbki z ostatnich 7 dni
- wartości z explainability API są widoczne bez błędów 500 po stronie dashboardu
