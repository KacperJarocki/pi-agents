import asyncio
import os
from datetime import datetime, UTC
from statistics import median

import pandas as pd

from .ml_core import FeatureExtractor, AnomalyDetector, get_all_recent_flows
from .ml_core import save_anomaly, save_behavior_alert, save_inference_result, update_device_risk_score, log


def _risk_from_score(score: float, threshold: float) -> float:
    # IsolationForest decision_function scores: lower => more anomalous.
    # Scale risk relative to the configured threshold so pre-threshold drift is visible,
    # and confirmed anomalies ramp quickly into the upper range.
    margin = threshold - score
    if margin <= 0:
        baseline = 35.0 * max(0.0, min(1.0, (threshold - score) / max(abs(threshold), 0.05) + 1.0))
        return round(max(0.0, min(35.0, baseline)), 4)

    threshold_scale = max(abs(threshold), 0.05)
    normalized = margin / threshold_scale
    risk = 35.0 + min(65.0, normalized * 45.0)
    return round(max(0.0, min(100.0, risk)), 4)


def _median(values: list[float], default: float = 0.0) -> float:
    cleaned = [float(v) for v in values if v is not None]
    return float(median(cleaned)) if cleaned else default


def _behavior_severity(score: float, critical_at: float) -> str:
    return "critical" if score >= critical_at else "warning"


def _build_behavior_alerts(
    device_id: int,
    latest_bucket: pd.DataFrame,
    history_buckets: pd.DataFrame,
    history_flows: pd.DataFrame,
) -> list[dict]:
    if latest_bucket.empty:
        return []

    alerts = []
    bucket_start = latest_bucket["bucket_start"].iloc[0] if not latest_bucket.empty else None

    latest_destinations = set(latest_bucket["dst_ip"].dropna().astype(str))
    previous_destinations = set(history_flows["dst_ip"].dropna().astype(str)) if not history_flows.empty else set()
    new_destinations = sorted(dest for dest in latest_destinations if dest not in previous_destinations)
    destination_ratio = len(new_destinations) / max(len(latest_destinations), 1)
    if len(new_destinations) >= 3 and destination_ratio >= 0.5:
        score = min(100.0, len(new_destinations) * 12.5 + destination_ratio * 25.0)
        alerts.append(
            {
                "device_id": device_id,
                "bucket_start": bucket_start,
                "alert_type": "destination_novelty",
                "severity": _behavior_severity(score, 70.0),
                "score": round(score, 2),
                "title": "New destination burst",
                "description": f"Device contacted {len(new_destinations)} new destinations in the latest bucket.",
                "evidence": {
                    "new_destinations": new_destinations[:10],
                    "new_destination_count": len(new_destinations),
                    "destination_ratio": round(destination_ratio, 4),
                },
            }
        )

    latest_dns_queries = int(latest_bucket["dns_query"].notna().sum())
    latest_unique_dns = int(latest_bucket["dns_query"].dropna().nunique())
    baseline_dns = _median(history_buckets.get("dns_queries", pd.Series(dtype=float)).tolist(), default=0.0)
    baseline_unique_dns = _median(
        [float(group["dns_query"].dropna().nunique()) for _, group in history_flows.groupby("bucket_start")],
        default=0.0,
    ) if not history_flows.empty else 0.0
    dns_ratio = latest_dns_queries / max(baseline_dns, 1.0)
    unique_dns_ratio = latest_unique_dns / max(baseline_unique_dns, 1.0)
    if latest_dns_queries >= max(6, baseline_dns * 3) and latest_unique_dns >= max(3, baseline_unique_dns * 2, 3):
        score = min(100.0, dns_ratio * 18.0 + unique_dns_ratio * 12.0)
        alerts.append(
            {
                "device_id": device_id,
                "bucket_start": bucket_start,
                "alert_type": "dns_burst",
                "severity": _behavior_severity(score, 75.0),
                "score": round(score, 2),
                "title": "DNS burst detected",
                "description": f"Device issued {latest_dns_queries} DNS queries across {latest_unique_dns} domains in the latest bucket.",
                "evidence": {
                    "dns_queries": latest_dns_queries,
                    "unique_dns_domains": latest_unique_dns,
                    "baseline_dns_queries": round(baseline_dns, 2),
                    "baseline_unique_dns_domains": round(baseline_unique_dns, 2),
                },
            }
        )

    latest_ports = int(latest_bucket["dst_port"].nunique())
    baseline_ports = _median(history_buckets.get("unique_ports", pd.Series(dtype=float)).tolist(), default=0.0)
    previous_ports = set(history_flows["dst_port"].dropna().astype(int).tolist()) if not history_flows.empty else set()
    new_ports = sorted(port for port in latest_bucket["dst_port"].dropna().astype(int).unique().tolist() if port not in previous_ports)
    port_ratio = latest_ports / max(baseline_ports, 1.0)
    if latest_ports >= max(8, baseline_ports * 3) or len(new_ports) >= 6:
        score = min(100.0, port_ratio * 16.0 + len(new_ports) * 6.0)
        alerts.append(
            {
                "device_id": device_id,
                "bucket_start": bucket_start,
                "alert_type": "port_churn",
                "severity": _behavior_severity(score, 72.0),
                "score": round(score, 2),
                "title": "Port churn detected",
                "description": f"Device touched {latest_ports} destination ports with {len(new_ports)} unseen ports in the latest bucket.",
                "evidence": {
                    "unique_ports": latest_ports,
                    "new_ports": new_ports[:12],
                    "baseline_unique_ports": round(baseline_ports, 2),
                },
            }
        )

    return alerts


def _risk_with_behavior(ml_risk: float, behavior_alerts: list[dict]) -> float:
    if not behavior_alerts:
        return ml_risk
    bonus = sum(min(25.0, float(alert["score"]) * 0.25) for alert in behavior_alerts)
    if len(behavior_alerts) >= 2:
        bonus += 10.0
    return round(min(100.0, ml_risk + bonus), 4)


async def run_inference_once(detector: AnomalyDetector, hours: int):
    flows = await get_all_recent_flows(hours=hours)
    if flows.empty:
        log.info("inference_no_data")
        return 0

    extractor = FeatureExtractor()
    features = extractor.extract_features(flows)
    baseline_hours = int(os.getenv("BEHAVIOR_BASELINE_HOURS", "168"))
    baseline_flows = await get_all_recent_flows(hours=baseline_hours)
    if not baseline_flows.empty:
        baseline_flows = baseline_flows.copy()
        baseline_flows["bucket_start"] = baseline_flows["timestamp"].dt.floor(f"{extractor.bucket_minutes}min")
    baseline_features = extractor.extract_features(baseline_flows)
    per_device_models = os.getenv("PER_DEVICE_MODELS", "true").lower() == "true"
    scored_results = []

    if per_device_models:
        for device_id, group in features.groupby('device_id'):
            latest = group.sort_values('bucket_start').tail(1)
            device_detector = AnomalyDetector(model_path=os.getenv("MODEL_PATH", "/data/models"))
            if not device_detector.load_model(device_id=int(device_id)):
                log.warning("inference_model_missing_for_device", device_id=int(device_id))
                continue
            scored_results.extend(
                {**row, "threshold": device_detector.threshold} for row in device_detector.score(latest)
            )
    else:
        scored_results = [{**row, "threshold": detector.threshold} for row in detector.score(features)]

    anomalies = []
    for a in scored_results:
        device_id = a["device_id"]
        score = a["anomaly_score"]
        is_anomaly = bool(a.get("is_anomaly"))
        severity = a["severity"]
        threshold = float(a.get("threshold", os.getenv("ANOMALY_THRESHOLD", "-0.5")))
        bucket_start = a.get("bucket_start")
        latest_bucket_flows = baseline_flows[
            (baseline_flows["device_id"] == device_id) & (baseline_flows["bucket_start"] == bucket_start)
        ] if bucket_start is not None and not baseline_flows.empty else pd.DataFrame()
        history_bucket_features = baseline_features[
            (baseline_features["device_id"] == device_id) & (baseline_features["bucket_start"] < bucket_start)
        ] if bucket_start is not None and not baseline_features.empty else pd.DataFrame()
        history_flows = baseline_flows[
            (baseline_flows["device_id"] == device_id) & (baseline_flows["bucket_start"] < bucket_start)
        ] if bucket_start is not None and not baseline_flows.empty else pd.DataFrame()
        behavior_alerts = _build_behavior_alerts(device_id, latest_bucket_flows, history_bucket_features, history_flows)
        ml_risk = _risk_from_score(score, threshold)
        risk_score = _risk_with_behavior(ml_risk, behavior_alerts)

        await update_device_risk_score(
            device_id=device_id,
            risk_score=risk_score,
            last_inference_score=float(score),
        )
        await save_inference_result(
            device_id=device_id,
            bucket_start=bucket_start,
            anomaly_score=float(score),
            risk_score=risk_score,
            is_anomaly=is_anomaly,
            severity=severity,
            features=a.get("features") or {},
            retention_days=7,
        )
        for alert in behavior_alerts:
            await save_behavior_alert(
                device_id=device_id,
                bucket_start=bucket_start,
                alert_type=alert["alert_type"],
                severity=alert["severity"],
                score=float(alert["score"]),
                title=alert["title"],
                description=alert["description"],
                evidence=alert["evidence"],
                retention_days=7,
            )

        log.info(
            "inference_device_score",
            device_id=device_id,
            score=float(score),
            ml_risk=ml_risk,
            risk_score=risk_score,
            threshold=threshold,
            behavior_alert_count=len(behavior_alerts),
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
        at=datetime.now(UTC).isoformat(),
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
