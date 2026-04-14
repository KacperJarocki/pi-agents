import asyncio
import os
from datetime import datetime

from sklearn.ensemble import IsolationForest

from .ml_core import FeatureExtractor, AnomalyDetector, get_all_recent_flows
from .ml_core import log


async def train_model():
    hours = int(os.getenv("TRAINING_HOURS", "168"))
    min_samples = int(os.getenv("MIN_TRAINING_SAMPLES", "100"))
    contamination = float(os.getenv("CONTAMINATION", "0.05"))

    flows = await get_all_recent_flows(hours=hours)
    if flows.empty:
        log.warning("training_no_data", hours=hours)
        return 0

    extractor = FeatureExtractor()
    features = extractor.extract_features(flows)

    if len(features) < 1:
        log.warning("training_no_features")
        return 0

    # We train on per-device aggregated feature rows.
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

    detector = AnomalyDetector(model_path=os.getenv("MODEL_PATH", "/data/models"))
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
