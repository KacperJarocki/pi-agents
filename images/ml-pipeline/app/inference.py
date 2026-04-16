import asyncio
import os
from datetime import datetime

from .ml_core import FeatureExtractor, AnomalyDetector, get_all_recent_flows
from .ml_core import save_anomaly, update_device_risk_score, log


def _risk_from_score(score: float) -> float:
    # IsolationForest decision_function scores: lower => more anomalous.
    # Map negative scores into a 0..100-ish risk for UI.
    return max(0.0, min(100.0, (-score) * 100.0))


async def run_inference_once(detector: AnomalyDetector, hours: int):
    flows = await get_all_recent_flows(hours=hours)
    if flows.empty:
        log.info("inference_no_data")
        return 0

    extractor = FeatureExtractor()
    features = extractor.extract_features(flows)
    per_device_models = os.getenv("PER_DEVICE_MODELS", "true").lower() == "true"
    scored_results = []

    if per_device_models:
        for device_id, group in features.groupby('device_id'):
            latest = group.sort_values('bucket_start').tail(1)
            device_detector = AnomalyDetector(model_path=os.getenv("MODEL_PATH", "/data/models"))
            if not device_detector.load_model(device_id=int(device_id)):
                log.warning("inference_model_missing_for_device", device_id=int(device_id))
                continue
            scored_results.extend(device_detector.score(latest))
    else:
        scored_results = detector.score(features)

    anomalies = []
    for a in scored_results:
        device_id = a["device_id"]
        score = a["anomaly_score"]
        is_anomaly = bool(a.get("is_anomaly"))
        severity = a["severity"]

        await update_device_risk_score(
            device_id=device_id,
            risk_score=_risk_from_score(score),
            last_inference_score=float(score),
        )

        log.info(
            "inference_device_score",
            device_id=device_id,
            score=float(score),
            risk_score=_risk_from_score(score),
            is_anomaly=is_anomaly,
        )

        if is_anomaly:
            anomalies.append(a)
            await save_anomaly(
                device_id=device_id,
                anomaly_type="isolation_forest",
                severity=severity,
                score=float(score),
                description=f"IsolationForest anomaly score={score:.4f}",
                features=a.get("features") or {},
            )

    log.info(
        "inference_complete",
        at=datetime.utcnow().isoformat(),
        devices=int(features.shape[0]),
        anomalies=len(anomalies),
    )

    return len(anomalies)


async def run_inference_loop():
    interval = int(os.getenv("INFERENCE_INTERVAL", "300"))
    hours = int(os.getenv("INFERENCE_HOURS", "24"))
    per_device_models = os.getenv("PER_DEVICE_MODELS", "true").lower() == "true"

    detector = AnomalyDetector(model_path=os.getenv("MODEL_PATH", "/data/models"))

    while True:
        try:
            if not per_device_models and detector.model is None:
                detector.load_model()
            if not per_device_models and detector.model is None:
                log.warning("inference_model_missing")
            else:
                await run_inference_once(detector, hours=hours)
        except Exception as e:
            log.error("inference_error", error=str(e))

        await asyncio.sleep(interval)


def main():
    asyncio.run(run_inference_loop())


if __name__ == "__main__":
    main()
