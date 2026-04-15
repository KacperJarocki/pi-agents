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
5. zapisuje anomaly i aktualizuje `risk_score`

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
