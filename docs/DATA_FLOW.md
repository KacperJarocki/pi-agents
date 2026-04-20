# Data Flow — End-to-end lineage danych

> Ostatnia aktualizacja: Kwiecień 2026

---

## Spis treści

1. [Od pakietu do toasta](#od-pakietu-do-toasta)
2. [Mapowanie: pola tshark → ML features](#mapowanie-pola-tshark--ml-features)
3. [Latencje](#latencje)
4. [Retencja danych](#retencja-danych)
5. [Schemat bazy danych](#schemat-bazy-danych)

---

## Od pakietu do toasta

Kompletna ścieżka danych — od momentu gdy urządzenie IoT wysyła pakiet WiFi
do momentu gdy użytkownik widzi alert na dashboardzie.

```
  ┌─────────────────────────────────────────────────────────────────┐
  │  1. CAPTURE (~2s)                                               │
  │                                                                 │
  │  Urządzenie IoT  ──WiFi──►  hostapd (gateway-agent)            │
  │                    │                                            │
  │                    │  NAT (iptables IOT_GATEWAY_NAT)            │
  │                    ▼                                            │
  │  tcpdump -i wlan0 -c 300 ──► /tmp/capture.pcap                 │
  │  tshark -r capture.pcap ──► 15 pól per pakiet                  │
  │                                                                 │
  └─────────────────────────┬───────────────────────────────────────┘
                            │
  ┌─────────────────────────▼───────────────────────────────────────┐
  │  2. COLLECT (~2-5s)                                             │
  │                                                                 │
  │  parse_tshark_line() → dict                                     │
  │  resolve_device() → device_id (DHCP lease / MAC / placeholder)  │
  │  bufor (150 flow-ów lub 2s)                                     │
  │       │                                                         │
  │       ▼                                                         │
  │  INSERT traffic_flows (batch)                                   │
  │  UPDATE devices.extra_data                                      │
  │                                                                 │
  └─────────────────────────┬───────────────────────────────────────┘
                            │
                            │  SQLite WAL  /data/iot-security.db
                            │
  ┌─────────────────────────▼───────────────────────────────────────┐
  │  3. TRAIN (co 30 min, CronJob)                                  │
  │                                                                 │
  │  SELECT traffic_flows WHERE timestamp >= now - 24h              │
  │  FeatureExtractor: 5-min buckets per device                     │
  │    (8 features: bytes, packets, destinations, ports,            │
  │     dns_queries, avg_bytes/pkt, packet_rate, conn_duration)     │
  │  AnomalyDetector.fit() per device (min 20 buckets)              │
  │  joblib.dump() → /data/models/..._device_{id}.joblib            │
  │  INSERT model_metadata                                          │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘
                            │
  ┌─────────────────────────▼───────────────────────────────────────┐
  │  4. INFERENCE (co 60s, Deployment loop)                         │
  │                                                                 │
  │  SELECT traffic_flows (24h recent + 168h baseline)              │
  │  FeatureExtractor → latest bucket per device                    │
  │  AnomalyDetector.score() → normalized 0–1                       │
  │  _build_behavior_alerts() → 9 heurystyk                         │
  │  _risk_with_contributors() → composite risk 0–100               │
  │       │                                                         │
  │       ▼  batch_save_inference_cycle():                          │
  │  UPDATE devices.risk_score                                      │
  │  INSERT device_inference_history                                │
  │  INSERT device_behavior_alerts (dedup by device+type+bucket)    │
  │  INSERT anomalies (if threshold breached)                       │
  │  run_retention_cleanup()                                        │
  │                                                                 │
  └─────────────────────────┬───────────────────────────────────────┘
                            │
  ┌─────────────────────────▼───────────────────────────────────────┐
  │  5. API (cache TTL 3-10s)                                       │
  │                                                                 │
  │  Gateway API (SQLAlchemy async + aiosqlite)                     │
  │  TTLCache (LRU, single-flight, 500 entries max)                 │
  │       │                                                         │
  │       │  GET /api/v1/devices → devices + risk_score             │
  │       │  GET /api/v1/alerts → anomalies + behavior_alerts       │
  │       │  GET /api/v1/metrics/* → aggregated views               │
  │       ▼                                                         │
  └─────────────────────────┬───────────────────────────────────────┘
                            │
  ┌─────────────────────────▼───────────────────────────────────────┐
  │  6. DASHBOARD (~30s polling)                                    │
  │                                                                 │
  │  Dashboard proxy → GET /api/v1/alerts (co 30s)                  │
  │  Dedup (seen_keys) → broadcast WS                               │
  │  Alpine.js → toast notification (auto-dismiss 10s)              │
  │                                                                 │
  │  HTMX partial refresh (co 30s): devices, timeline, anomalies   │
  │  Device detail JS (co 5s): risk score, behavior alerts          │
  │                                                                 │
  └─────────────────────────────────────────────────────────────────┘
```

---

## Mapowanie: pola tshark → ML features

Jak surowe pola z tshark (15 pól per pakiet) stają się 8 feature-ami ML?

### Krok 1: tshark → traffic_flows

| Pole tshark | → | Kolumna traffic_flows |
|-------------|---|----------------------|
| `ip.src` | → | `src_ip` |
| `ip.dst` | → | `dst_ip` |
| `tcp.srcport` / `udp.srcport` | → | `src_port` |
| `tcp.dstport` / `udp.dstport` | → | `dst_port` |
| `_ws.col.Protocol` | → | `protocol` |
| `frame.len` | → | `bytes_sent` |
| `eth.addr` | → | *(resolve_device → device_id)* |
| `dns.qry.name` | → | `dns_query` |
| `dns.flags.rcode` | → | `flags.dns_rcode` (JSON) |
| `icmp.type` | → | `flags.icmp_type` (JSON) |
| `icmp.code` | → | `flags.icmp_code` (JSON) |
| `tcp.len` | → | *(nieużywane)* |
| `udp.length` | → | *(nieużywane)* |

### Krok 2: traffic_flows → feature buckets

FeatureExtractor grupuje flow-y w buckety po `FEATURE_BUCKET_MINUTES` minut (domyślnie 2 w K8s)
per device_id:

| Feature ML | Jak obliczane | Z jakich kolumn |
|------------|---------------|-----------------|
| `total_bytes` | `SUM(bytes_sent)` | `bytes_sent` |
| `total_packets` | `COUNT(*)` | — (1 per row) |
| `unique_destinations` | `COUNT(DISTINCT dst_ip)` | `dst_ip` |
| `unique_ports` | `COUNT(DISTINCT dst_port)` | `dst_port` |
| `dns_queries` | `COUNT(WHERE dns_query IS NOT NULL)` | `dns_query` |
| `avg_bytes_per_packet` | `total_bytes / total_packets` | `bytes_sent` |
| `packet_rate` | `total_packets / bucket_minutes` | — |
| `connection_duration_avg` | `AVG(duration_ms)` | `duration_ms` (zawsze 0 w MVP) |

**Uwaga**: `connection_duration_avg` jest zawsze 0 w MVP, bo collector nie mierzy czasu trwania
połączenia (każdy pakiet = osobny rekord z `duration_ms=0`).

### Krok 3: feature buckets → scoring

AnomalyDetector używa 8 features z bucketu do obliczenia anomaly score (0–1).
Score jest następnie łączony z behavior alerts i protocol signals w composite risk score (0–100).

Szczegóły algorytmów ML → [ML Pipeline](ML_PIPELINE.md)

---

## Latencje

End-to-end latencja od pakietu do alertu na dashboardzie:

| Etap | Latencja | Co ją determinuje |
|------|----------|-------------------|
| Capture (tcpdump) | ~2s | `CAPTURE_TIMEOUT` (2s), `CAPTURE_PACKET_COUNT` (300) |
| Buffer + flush | ~2-5s | `FLUSH_INTERVAL` (2s), `BATCH_SIZE` (150) |
| **Capture → DB** | **~4-7s** | Suma powyższych |
| Inference cycle | ~60s | `INFERENCE_INTERVAL` (60s w K8s) |
| **DB → anomaly/alert** | **~60s** | Interwał inference |
| API cache | 3-5s | TTL cache (3-5s) |
| Dashboard WS poll | ~30s | `REFRESH_INTERVAL` (30s) |
| **Anomaly → toast** | **~35s** | Cache TTL + poll interval |
| **End-to-end** | **~100-105s** | Capture→DB + inference + API + dashboard |

### Szybsze wykrywanie

Aby zmniejszyć end-to-end latencję:
- `INFERENCE_INTERVAL=30` → inference co 30s (kosztem CPU)
- `REFRESH_INTERVAL=10` → dashboard poll co 10s (kosztem sieci)
- Razem: ~50-60s end-to-end

---

## Retencja danych

| Tabela | Retencja | Mechanizm | Kto czyści |
|--------|----------|-----------|------------|
| `traffic_flows` | 7 dni | `DELETE WHERE timestamp < now - 7d` | ml-inference (retention cleanup) |
| `device_inference_history` | 7 dni | `DELETE WHERE timestamp < now - 7d` | ml-inference |
| `device_behavior_alerts` | 14 dni | `DELETE WHERE timestamp < now - 14d` | ml-inference |
| `anomalies` | Auto-resolve 48h | `UPDATE SET resolved=1 WHERE timestamp < now - 48h` | ml-inference |
| `model_metadata` | Ostatnie 10 per device+type | `DELETE` nadmiarowych | ml-inference |
| `devices` | Bez limitu | — | — |

### Batch cleanup

Retention cleanup działa w batchach po **5000 wierszy** (commit po każdym batchu),
żeby nie blokować bazy na długo. Uruchamiana na końcu każdego cyklu inference.

---

## Schemat bazy danych

Jedna baza SQLite: `/data/iot-security.db` (WAL mode, `busy_timeout=5000ms`).

### devices

| Kolumna | Typ | Uwagi |
|---------|-----|-------|
| `id` | INTEGER PK | Auto-increment |
| `mac_address` | TEXT UNIQUE | Fallback `ip:<ip>` gdy MAC niedostępny |
| `ip_address` | TEXT | Aktualny IP z DHCP lease |
| `hostname` | TEXT | Z dnsmasq lease lub None |
| `device_type` | TEXT | Nieużywane w MVP |
| `first_seen` | TIMESTAMP | Ustawiane przy INSERT |
| `last_seen` | TIMESTAMP | Aktualizowane przy każdym flush |
| `is_active` | INTEGER | 1 = aktywne |
| `risk_score` | REAL | Composite risk 0–100 |
| `last_inference_score` | REAL | Surowy IsolationForest score |
| `last_inference_at` | TIMESTAMP | Ostatni zapis ml-inference |
| `extra_data` | TEXT (JSON) | Zagregowane statystyki z collectora |

### traffic_flows

| Kolumna | Typ | Uwagi |
|---------|-----|-------|
| `id` | INTEGER PK | |
| `device_id` | INTEGER FK | → devices.id |
| `timestamp` | TIMESTAMP | Czas przechwycenia pakietu |
| `src_ip` / `dst_ip` | TEXT | |
| `src_port` / `dst_port` | INTEGER | 0 dla ICMP |
| `protocol` | TEXT | TCP / UDP / ICMP / … |
| `bytes_sent` | INTEGER | frame.len z tshark |
| `bytes_received` | INTEGER | Zawsze 0 (collector pisze tylko bytes_sent) |
| `packets` | INTEGER | Domyślnie 1 per rekord |
| `duration_ms` | INTEGER | Domyślnie 0 |
| `dns_query` | TEXT | dns.qry.name (jeśli DNS) |
| `flags` | TEXT (JSON) | `{dns_rcode, icmp_type, icmp_code}` |

### anomalies

| Kolumna | Typ | Uwagi |
|---------|-----|-------|
| `id` | INTEGER PK | |
| `device_id` | INTEGER FK | |
| `timestamp` | TIMESTAMP | |
| `anomaly_type` | TEXT | Typ modelu (np. `isolation_forest`) |
| `severity` | TEXT | `warning` / `critical` |
| `score` | REAL | Decision score modelu |
| `description` | TEXT | Opis czytelny dla człowieka |
| `features` | TEXT (JSON) | Snapshot feature-ów w momencie detekcji |
| `resolved` | INTEGER | 0 = otwarta; auto-resolve po 48h |
| `resolved_at` | TIMESTAMP | |

### device_inference_history

| Kolumna | Typ | Uwagi |
|---------|-----|-------|
| `id` | INTEGER PK | |
| `device_id` | INTEGER FK | |
| `timestamp` | TIMESTAMP | Czas zapisu |
| `bucket_start` | TIMESTAMP | Początek 5-min bucketu |
| `anomaly_score` | REAL | |
| `risk_score` | REAL | Composite 0–100 |
| `is_anomaly` | INTEGER | 1 jeśli próg przekroczony |
| `severity` | TEXT | |
| `features` | TEXT (JSON) | Pełny feature + risk breakdown |

### device_behavior_alerts

| Kolumna | Typ | Uwagi |
|---------|-----|-------|
| `id` | INTEGER PK | |
| `device_id` | INTEGER FK | |
| `timestamp` | TIMESTAMP | |
| `bucket_start` | TIMESTAMP | Klucz dedup (device+type+bucket) |
| `alert_type` | TEXT | Patrz typy alertów niżej |
| `severity` | TEXT | `warning` / `critical` |
| `score` | REAL | Heuristic score 0–100 |
| `title` | TEXT | |
| `description` | TEXT | |
| `evidence` | TEXT (JSON) | Surowe dane wspierające alert |
| `resolved` | INTEGER | 0 = otwarta |

**Typy alertów**: `destination_novelty`, `dns_burst`, `port_churn`, `traffic_pattern_drift`,
`beaconing_suspected`, `dns_failure_spike`, `dns_nxdomain_burst`, `icmp_sweep_suspected`,
`icmp_echo_fanout`

### model_metadata

| Kolumna | Typ | Uwagi |
|---------|-----|-------|
| `id` | INTEGER PK | |
| `device_id` | INTEGER | NULL dla modeli globalnych |
| `model_type` | TEXT | isolation_forest / lof / ocsvm / autoencoder |
| `version` | TEXT | Legacy NOT NULL, ustawiane na "1.0" |
| `trained_at` | TIMESTAMP | Czas zakończenia treningu |
| `training_samples` | INTEGER | Liczba feature buckets |
| `features_count` | INTEGER | Liczba features |
| `contamination` | REAL | Adaptive contamination rate |
| `threshold` | REAL | Próg decision function |
| `score_mean` / `score_std` | REAL | Statystyki do normalizacji score |
| `accuracy` | REAL | |
| `features_used` | TEXT | |
| `parameters` | TEXT (JSON) | Pełne parametry modelu |
| `is_active` | INTEGER | 0/1 |
| `training_hours` | INTEGER | Godziny danych treningowych |

### global_training_config

| Kolumna | Typ | Uwagi |
|---------|-----|-------|
| `id` | INTEGER PK | Zawsze 1 (singleton) |
| `model_type` | TEXT | Domyślny algorytm |
| `training_hours` | INTEGER | Godziny danych treningowych |
| `min_samples` | INTEGER | Minimum buckets required |
| `bucket_minutes` | INTEGER | Szerokość bucketu |
| `contamination_min` / `max` | REAL | Zakres adaptive contamination |
| `updated_at` | TIMESTAMP | |

### device_training_config

| Kolumna | Typ | Uwagi |
|---------|-----|-------|
| `id` | INTEGER PK | |
| `device_id` | INTEGER UNIQUE | → devices.id |
| `model_type` | TEXT | Override algorytmu |
| `training_hours` | INTEGER | Override okna treningowego |
| `min_samples` | INTEGER | Override minimum buckets |
| `bucket_minutes` | INTEGER | Override szerokości bucketu |
| `contamination_min` / `max` | REAL | Override zakresu contamination |
| `enabled` | INTEGER | 1 = użyj overrides |
| `updated_at` | TIMESTAMP | |

### Relacje

```
devices ◄──── traffic_flows         (device_id FK)
devices ◄──── anomalies              (device_id FK)
devices ◄──── device_inference_history (device_id FK)
devices ◄──── device_behavior_alerts  (device_id FK)
devices ◄──── device_training_config  (device_id UNIQUE)
devices ◄──── model_metadata          (device_id, NULL=global)
```

Brak CASCADE — usunięcie device'a nie usuwa powiązanych rekordów automatycznie.

---

## Powiązane dokumenty

- [Collector](COLLECTOR.md) — szczegóły pipelienu przechwytywania
- [ML Pipeline](ML_PIPELINE.md) — algorytmy ML, scoring, risk composition
- [Gateway Agent](GATEWAY_AGENT.md) — DHCP leases, NAT
- [Gateway API](GATEWAY_API.md) — endpointy, cache, migracje
- [Dashboard](DASHBOARD.md) — widoki, WS alerts
- [Infrastructure](INFRASTRUCTURE.md) — K8s manifesty, PVC, CI/CD
