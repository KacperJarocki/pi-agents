import asyncio
import os
from datetime import datetime

from sklearn.ensemble import IsolationForest

from .ml_core import (
    FeatureExtractor, AnomalyDetector, get_all_recent_flows,
    get_detector, get_device_model_configs, ensure_schema, log,
    AVAILABLE_MODEL_TYPES,
)


async def train_model():
    hours = int(os.getenv("TRAINING_HOURS", "48"))
    min_samples = int(os.getenv("MIN_TRAINING_SAMPLES", "10"))
    contamination = float(os.getenv("CONTAMINATION", "0.05"))
    per_device_models = os.getenv("PER_DEVICE_MODELS", "true").lower() == "true"
    default_model_type = os.getenv("MODEL_TYPE", "isolation_forest")

    # Ensure schema (creates device_model_config table if missing)
    await ensure_schema()

    flows = await get_all_recent_flows(hours=hours)
    if flows.empty:
        log.warning("training_no_data", hours=hours)
        return 0

    extractor = FeatureExtractor()
    features = extractor.extract_features(flows)

    log.info(
        "training_dataset_stats",
        flow_count=int(len(flows)),
        device_count=int(flows['device_id'].nunique()) if not flows.empty else 0,
        sample_count=int(len(features)),
        per_device_models=per_device_models,
    )

    if len(features) < 1:
        log.warning("training_no_features")
        return 0

    # Load per-device model type configs (used only for logging active model)
    device_model_configs = await get_device_model_configs()

    if per_device_models:
        trained_devices = 0

        for device_id, group in features.groupby('device_id'):
            samples = int(len(group))
            if samples < min_samples:
                log.warning(
                    "training_not_enough_samples_for_device",
                    device_id=int(device_id),
                    samples=samples,
                    min_samples=min_samples,
                )
                continue

            X = group[FeatureExtractor.FEATURE_COLUMNS].values
            adaptive_contamination = max(0.03, min(0.1, 5.0 / samples))
            device_trained = False

            # Train ALL model types per device
            for model_type in AVAILABLE_MODEL_TYPES:
                try:
                    detector = get_detector(model_type, model_path=os.getenv("MODEL_PATH", "/data/models"))

                    # Build kwargs based on model type
                    fit_kwargs = {"contamination": adaptive_contamination}
                    if model_type == "isolation_forest":
                        fit_kwargs["n_estimators"] = int(os.getenv("N_ESTIMATORS", "200"))
                    elif model_type == "lof":
                        fit_kwargs["n_neighbors"] = min(20, max(5, samples // 5))
                    elif model_type == "ocsvm":
                        fit_kwargs["nu"] = adaptive_contamination

                    detector.fit(X, **fit_kwargs)
                    detector.save_model(detector.model, device_id=int(device_id))
                    device_trained = True

                    log.info(
                        "training_complete_for_device",
                        trained_at=datetime.utcnow().isoformat(),
                        device_id=int(device_id),
                        samples=samples,
                        features=len(FeatureExtractor.FEATURE_COLUMNS),
                        model_type=model_type,
                    )
                except Exception as exc:
                    log.error(
                        "training_failed_for_model",
                        device_id=int(device_id),
                        model_type=model_type,
                        error=str(exc),
                    )

            if device_trained:
                trained_devices += 1

        if trained_devices == 0:
            log.warning("training_no_device_models", sample_count=int(len(features)), min_samples=min_samples)
        return trained_devices

    # Global model (non per-device)
    X = features[FeatureExtractor.FEATURE_COLUMNS].values
    if X.shape[0] < min_samples:
        log.warning(
            "training_not_enough_samples",
            samples=int(X.shape[0]),
            min_samples=min_samples,
        )
        return int(X.shape[0])

    detector = get_detector(default_model_type, model_path=os.getenv("MODEL_PATH", "/data/models"))
    detector.fit(X, contamination=contamination, n_estimators=int(os.getenv("N_ESTIMATORS", "200")))
    detector.save_model(detector.model)

    log.info(
        "training_complete",
        trained_at=datetime.utcnow().isoformat(),
        samples=int(X.shape[0]),
        features=len(FeatureExtractor.FEATURE_COLUMNS),
        model_type=default_model_type,
    )

    return int(X.shape[0])


def main():
    asyncio.run(train_model())


if __name__ == "__main__":
    main()
