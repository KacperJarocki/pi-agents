# ml-pipeline

`ml-pipeline` odpowiada za trening i inferencję modeli anomalii.

## Model MVP

- algorytm: Isolation Forest
- podejście: bucketed per-device samples
- tryb: modele per urządzenie

## Training

Trainer:

1. czyta flow z SQLite
2. buduje bucketed feature samples
3. grupuje dane per `device_id`
4. trenuje model tylko dla urządzeń z wystarczającą liczbą próbek
5. zapisuje model jako `isolation_forest_model_device_<id>.joblib`

## Inference

Inferencja:

1. czyta najnowsze flow
2. buduje bucketed samples
3. bierze najnowszy bucket dla każdego urządzenia
4. ładuje model per-device
5. buduje adaptive baseline z historii inferencji i flow
6. uruchamia heurystyki behavior
7. zapisuje anomaly i aktualizuje `risk_score`

## Heurystyki Behavior

Aktualnie inference dopina do ML także:

- `destination_novelty`
- `dns_burst`
- `port_churn`
- `traffic_pattern_drift`
- `beaconing_suspected`
- `dns_failure_spike`
- `dns_nxdomain_burst`
- `icmp_sweep_suspected`
- `icmp_echo_fanout`

Wyniki trafiają do `device_behavior_alerts`, a najnowsze cechy do `device_inference_history`.

## Risk Engine v2

Finalny `risk_score` jest składany z:

- `ml_risk`
- `behavior_risk`
- `protocol_risk`
- `correlation_bonus`

Breakdown jest zapisywany do `device_inference_history.features`, żeby API i dashboard mogły pokazać delta risk i top reason bez dodatkowej migracji DB.

## SQLite Optimization

ML pipeline używa aiosqlite z trybem WAL dla lepszej wydajności:

- `PRAGMA journal_mode=WAL` — writers nie blokują readers
- `PRAGMA synchronous=NORMAL` — balans spójność/wydajność
- `PRAGMA busy_timeout=5000` — retry przy write contention

Indeksy używane przez ML:

- `idx_flows_timestamp` — globalne time-range queries
- `idx_inference_history_device_time` — odczyt baseline z historii
- `idx_behavior_alert_device_time` — szybkie per-device alert queries

## Kluczowe env

- `TRAINING_HOURS`
- `MIN_TRAINING_SAMPLES`
- `FEATURE_BUCKET_MINUTES`
- `PER_DEVICE_MODELS`
- `INFERENCE_INTERVAL`
- `INFERENCE_HOURS`

## Jak sprawdzić gotowość modelu

1. logi trenera
2. `/api/v1/metrics/ml-status`
3. logi `ml-inference`

## Troubleshooting

Najważniejsze wpisy w logach:

- `training_dataset_stats`
- `training_complete_for_device`
- `training_not_enough_samples_for_device`
- `model_loaded`
- `inference_model_missing_for_device`
- `inference_complete`
