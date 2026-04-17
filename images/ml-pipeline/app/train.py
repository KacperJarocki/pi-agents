import asyncio
import os
from datetime import datetime

from sklearn.ensemble import IsolationForest

from .ml_core import FeatureExtractor, AnomalyDetector, get_all_recent_flows
from .ml_core import log


async def train_model():
    hours = int(os.getenv("TRAINING_HOURS", "48"))
    min_samples = int(os.getenv("MIN_TRAINING_SAMPLES", "10"))
    contamination = float(os.getenv("CONTAMINATION", "0.05"))
    per_device_models = os.getenv("PER_DEVICE_MODELS", "true").lower() == "true"

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

    detector = AnomalyDetector(model_path=os.getenv("MODEL_PATH", "/data/models"))

    if per_device_models:
        trained_devices = 0
        total_samples = 0

        for device_id, group in features.groupby('device_id'):
            samples = int(len(group))
            total_samples += samples
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
            model = IsolationForest(
                n_estimators=int(os.getenv("N_ESTIMATORS", "200")),
                contamination=adaptive_contamination,
                random_state=42,
                n_jobs=1,
            )
            model.fit(X)
            detector.save_model(model, device_id=int(device_id))
            trained_devices += 1

            log.info(
                "training_complete_for_device",
                trained_at=datetime.utcnow().isoformat(),
                device_id=int(device_id),
                samples=samples,
                features=len(FeatureExtractor.FEATURE_COLUMNS),
            )

        if trained_devices == 0:
            log.warning("training_no_device_models", sample_count=int(len(features)), min_samples=min_samples)
        return trained_devices

    X = features[FeatureExtractor.FEATURE_COLUMNS].values
    if X.shape[0] < min_samples:
        log.warning(
            "training_not_enough_samples",
            samples=int(X.shape[0]),
            min_samples=min_samples,
        )
        return int(X.shape[0])

    model = IsolationForest(
        n_estimators=int(os.getenv("N_ESTIMATORS", "200")),
        contamination=contamination,
        random_state=42,
        n_jobs=1,
    )
    model.fit(X)
    detector.save_model(model)

    log.info(
        "training_complete",
        trained_at=datetime.utcnow().isoformat(),
        samples=int(X.shape[0]),
        features=len(FeatureExtractor.FEATURE_COLUMNS),
    )

    return int(X.shape[0])


def main():
    asyncio.run(train_model())


if __name__ == "__main__":
    main()
