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
