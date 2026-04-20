import asyncio
import hashlib
import os
from datetime import datetime, UTC
from statistics import median, pstdev

import pandas as pd

from .ml_core import FeatureExtractor, AnomalyDetector, get_all_recent_flows
from .ml_core import log
from .ml_core import batch_save_inference_cycle, ensure_schema, DB_PATH, get_detector, get_device_model_configs
from .ml_core import AVAILABLE_MODEL_TYPES, batch_save_model_scores
import aiosqlite


# ── Feature extraction cache ────────────────────────────────────────────────
# Hash-based: we hash the (device_id, flow_count, first_ts, last_ts) tuple to
# detect whether the underlying flows have changed since the last cycle.
# This avoids re-running the groupby aggregation when no new flows arrived.

_feature_cache: dict[str, tuple[str, pd.DataFrame]] = {}  # cache_key → (hash, features_df)


def _flows_hash(flows: pd.DataFrame) -> str:
    """Compute a lightweight hash of a flows DataFrame for cache invalidation."""
    if flows.empty:
        return "empty"
    n = len(flows)
    first = str(flows["timestamp"].iloc[0])
    last = str(flows["timestamp"].iloc[-1])
    total_bytes = int(flows["bytes_sent"].sum()) if "bytes_sent" in flows.columns else 0
    key = f"{n}:{first}:{last}:{total_bytes}"
    return hashlib.md5(key.encode()).hexdigest()


def _cached_extract(extractor: FeatureExtractor, flows: pd.DataFrame, cache_key: str) -> pd.DataFrame:
    """Return cached features if flows haven't changed, otherwise extract fresh."""
    h = _flows_hash(flows)
    cached = _feature_cache.get(cache_key)
    if cached is not None and cached[0] == h:
        return cached[1]
    features = extractor.extract_features(flows)
    _feature_cache[cache_key] = (h, features)
    return features


PROTOCOL_ALERT_TYPES = {
    "dns_failure_spike",
    "dns_nxdomain_burst",
    "icmp_sweep_suspected",
    "icmp_echo_fanout",
}


def _risk_from_score(score: float, threshold: float) -> float:
    # IsolationForest decision_function scores: lower => more anomalous.
    # Scale risk relative to the configured threshold so pre-threshold drift is visible,
    # and confirmed anomalies ramp quickly into the upper range.
    margin = threshold - score
    if margin <= 0:
        # Normal branch: score >= threshold (not anomalous).
        # Use 2x abs(threshold) as the window so typical positive scores (0.05–0.3)
        # still produce a small but visible baseline risk (2–15%) instead of zero.
        window = 2.0 * max(abs(threshold), 0.05)
        baseline = 35.0 * max(0.0, min(1.0, (threshold - score) / window + 1.0))
        return round(max(0.0, min(35.0, baseline)), 4)

    threshold_scale = max(abs(threshold), 0.05)
    normalized = margin / threshold_scale
    risk = 35.0 + min(65.0, normalized * 45.0)
    return round(max(0.0, min(100.0, risk)), 4)


def _median(values: list[float], default: float = 0.0) -> float:
    cleaned = [float(v) for v in values if v is not None]
    return float(median(cleaned)) if cleaned else default


def _percentile(values: list[float], p: float, default: float = 0.0) -> float:
    cleaned = sorted(float(v) for v in values if v is not None)
    if not cleaned:
        return default
    idx = min(len(cleaned) - 1, max(0, int(round((len(cleaned) - 1) * p))))
    return float(cleaned[idx])


def _baseline_stats(values: list[float]) -> dict:
    cleaned = [float(v) for v in values if v is not None]
    if not cleaned:
        return {"median": 0.0, "p95": 0.0}
    return {
        "median": _median(cleaned, 0.0),
        "p95": _percentile(cleaned, 0.95, 0.0),
    }


def _behavior_severity(score: float, critical_at: float) -> str:
    return "critical" if score >= critical_at else "warning"


def _alert_category(alert_type: str) -> str:
    return "protocol" if alert_type in PROTOCOL_ALERT_TYPES else "behavior"


def _alert_weight(alert_type: str) -> float:
    if alert_type in {"beaconing_suspected", "destination_novelty"}:
        return 0.2
    if alert_type in {"dns_failure_spike", "dns_nxdomain_burst", "icmp_sweep_suspected", "icmp_echo_fanout"}:
        return 0.16
    return 0.18


def _risk_with_contributors(ml_risk: float, behavior_alerts: list[dict]) -> dict:
    if not behavior_alerts:
        return {
            "ml_risk": round(ml_risk, 4),
            "behavior_risk": 0.0,
            "protocol_risk": 0.0,
            "correlation_bonus": 0.0,
            "final_risk": round(ml_risk, 4),
            "top_reason": "Model score within baseline",
            "reason_summary": ["No active behavior or protocol contributors"],
        }

    strongest_by_type = {}
    for alert in behavior_alerts:
        alert_type = alert["alert_type"]
        existing = strongest_by_type.get(alert_type)
        if existing is None or float(alert["score"]) > float(existing["score"]):
            strongest_by_type[alert_type] = alert

    behavior_risk = 0.0
    protocol_risk = 0.0
    contribution_reasons = []
    categories = set()
    for alert_type, alert in strongest_by_type.items():
        raw_score = float(alert["score"])
        weighted = min(20.0 if _alert_category(alert_type) == "behavior" else 16.0, raw_score * _alert_weight(alert_type))
        category = _alert_category(alert_type)
        categories.add(category)
        if category == "protocol":
            protocol_risk += weighted
        else:
            behavior_risk += weighted
        contribution_reasons.append((weighted, alert["title"]))

    behavior_risk = min(35.0, behavior_risk)
    protocol_risk = min(20.0, protocol_risk)

    correlation_bonus = 0.0
    if ml_risk >= 20.0 and strongest_by_type:
        correlation_bonus += 8.0
    if len([alert_type for alert_type in strongest_by_type if _alert_category(alert_type) == "behavior"]) >= 2:
        correlation_bonus += 6.0
    if "behavior" in categories and "protocol" in categories:
        correlation_bonus += 6.0
    correlation_bonus = min(15.0, correlation_bonus)

    contribution_reasons.append((ml_risk, "Model drift versus device baseline"))
    if correlation_bonus > 0:
        contribution_reasons.append((correlation_bonus, "Correlated signals across ML and heuristics"))
    contribution_reasons.sort(key=lambda item: item[0], reverse=True)
    reason_summary = [reason for _, reason in contribution_reasons[:3]]

    return {
        "ml_risk": round(ml_risk, 4),
        "behavior_risk": round(behavior_risk, 4),
        "protocol_risk": round(protocol_risk, 4),
        "correlation_bonus": round(correlation_bonus, 4),
        "final_risk": round(min(100.0, ml_risk + behavior_risk + protocol_risk + correlation_bonus), 4),
        "top_reason": reason_summary[0] if reason_summary else "Model score within baseline",
        "reason_summary": reason_summary,
    }


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
    baseline_destinations = _baseline_stats(history_buckets.get("unique_destinations", pd.Series(dtype=float)).tolist())
    baseline_dns = _baseline_stats(history_buckets.get("dns_queries", pd.Series(dtype=float)).tolist())
    baseline_ports = _baseline_stats(history_buckets.get("unique_ports", pd.Series(dtype=float)).tolist())
    baseline_packet_rate = _baseline_stats(history_buckets.get("packet_rate", pd.Series(dtype=float)).tolist())

    # Pre-compute per-bucket groupby once to avoid 4 repeated groupby calls below
    _history_by_bucket: dict = {}
    if not history_flows.empty and "bucket_start" in history_flows.columns:
        _history_by_bucket = {k: v for k, v in history_flows.groupby("bucket_start")}

    latest_destinations = set(latest_bucket["dst_ip"].dropna().astype(str))
    latest_destination_count = float(len(latest_destinations))
    previous_destinations = set(history_flows["dst_ip"].dropna().astype(str)) if not history_flows.empty else set()
    new_destinations = sorted(dest for dest in latest_destinations if dest not in previous_destinations)
    destination_ratio = len(new_destinations) / max(len(latest_destinations), 1)
    if len(new_destinations) >= max(2, int(baseline_destinations["p95"] + 1)) and destination_ratio >= 0.4:
        score = min(100.0, len(new_destinations) * 12.5 + destination_ratio * 25.0 + latest_destination_count * 4.0)
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
                    "baseline_unique_destinations_median": round(baseline_destinations["median"], 2),
                    "baseline_unique_destinations_p95": round(baseline_destinations["p95"], 2),
                },
            }
        )

    latest_dns_queries = int(latest_bucket["dns_query"].notna().sum())
    latest_unique_dns = int(latest_bucket["dns_query"].dropna().nunique())
    baseline_unique_dns = _median(
        [float(grp["dns_query"].dropna().nunique()) for grp in _history_by_bucket.values()],
        default=0.0,
    ) if _history_by_bucket else 0.0
    dns_ratio = latest_dns_queries / max(baseline_dns["median"], 1.0)
    unique_dns_ratio = latest_unique_dns / max(baseline_unique_dns, 1.0)
    if latest_dns_queries >= max(5, baseline_dns["p95"] + 2, baseline_dns["median"] * 2) and latest_unique_dns >= max(3, baseline_unique_dns * 2, 3):
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
                    "baseline_dns_queries": round(baseline_dns["median"], 2),
                    "baseline_dns_queries_p95": round(baseline_dns["p95"], 2),
                    "baseline_unique_dns_domains": round(baseline_unique_dns, 2),
                },
            }
        )

    latest_ports = int(latest_bucket["dst_port"].nunique())
    previous_ports = set(history_flows["dst_port"].dropna().astype(int).tolist()) if not history_flows.empty else set()
    new_ports = sorted(port for port in latest_bucket["dst_port"].dropna().astype(int).unique().tolist() if port not in previous_ports)
    port_ratio = latest_ports / max(baseline_ports["median"], 1.0)
    if latest_ports >= max(6, baseline_ports["p95"] + 2, baseline_ports["median"] * 2) or len(new_ports) >= 5:
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
                    "baseline_unique_ports": round(baseline_ports["median"], 2),
                    "baseline_unique_ports_p95": round(baseline_ports["p95"], 2),
                },
            }
        )

    packet_rate = 0.0
    if len(latest_bucket) > 1:
        span = (latest_bucket["timestamp"].max() - latest_bucket["timestamp"].min()).total_seconds()
        packet_rate = float(len(latest_bucket) / span) if span > 0 else float(len(latest_bucket))
    if packet_rate >= max(1.5, baseline_packet_rate["p95"] * 1.5) and latest_destination_count >= max(3.0, baseline_destinations["median"] + 2.0):
        score = min(100.0, packet_rate * 8.0 + latest_destination_count * 6.0)
        alerts.append(
            {
                "device_id": device_id,
                "bucket_start": bucket_start,
                "alert_type": "traffic_pattern_drift",
                "severity": _behavior_severity(score, 70.0),
                "score": round(score, 2),
                "title": "Traffic pattern drift",
                "description": f"Packet rate {packet_rate:.2f} and {int(latest_destination_count)} destinations exceeded the recent baseline.",
                "evidence": {
                    "packet_rate": round(packet_rate, 4),
                    "baseline_packet_rate_median": round(baseline_packet_rate["median"], 4),
                    "baseline_packet_rate_p95": round(baseline_packet_rate["p95"], 4),
                    "unique_destinations": int(latest_destination_count),
                    "baseline_unique_destinations_median": round(baseline_destinations["median"], 2),
                    "baseline_unique_destinations_p95": round(baseline_destinations["p95"], 2),
                },
            }
        )

    if not history_flows.empty:
        latest_dest_set = set(latest_bucket["dst_ip"].dropna().astype(str))
        for dst_ip, group in history_flows.groupby("dst_ip"):
            if dst_ip not in latest_dest_set:
                continue
            timestamps = sorted(group["timestamp"].tolist())
            if len(timestamps) < 5:
                continue
            intervals = []
            for prev, cur in zip(timestamps, timestamps[1:]):
                delta = (cur - prev).total_seconds()
                if delta > 0:
                    intervals.append(delta)
            if len(intervals) < 4:
                continue
            interval_median = _median(intervals, 0.0)
            if interval_median < 5 or interval_median > 300:
                continue
            interval_std = float(pstdev(intervals)) if len(intervals) > 1 else 0.0
            regularity = interval_std / max(interval_median, 1.0)
            avg_bytes = float(group["bytes_sent"].fillna(0).mean()) if "bytes_sent" in group else 0.0
            if regularity <= 0.35 and avg_bytes <= 500:
                score = min(100.0, (1.0 - regularity) * 70.0 + max(0.0, 50.0 - avg_bytes / 10.0))
                alerts.append(
                    {
                        "device_id": device_id,
                        "bucket_start": bucket_start,
                        "alert_type": "beaconing_suspected",
                        "severity": _behavior_severity(score, 78.0),
                        "score": round(score, 2),
                        "title": "Beaconing suspected",
                        "description": f"Device shows regular low-volume traffic to {dst_ip} every ~{interval_median:.1f}s.",
                        "evidence": {
                            "dst_ip": dst_ip,
                            "interval_median_seconds": round(interval_median, 2),
                            "interval_std_seconds": round(interval_std, 2),
                            "regularity_ratio": round(regularity, 4),
                            "average_bytes": round(avg_bytes, 2),
                            "sample_count": len(intervals) + 1,
                        },
                    }
                )
                break

    latest_dns_failures = int((latest_bucket.get("dns_rcode", pd.Series(dtype=float)).fillna(0).astype(float) > 0).sum())
    historical_dns_failures = []
    if _history_by_bucket and "dns_rcode" in history_flows.columns:
        for grp in _history_by_bucket.values():
            historical_dns_failures.append(float((grp.get("dns_rcode", pd.Series(dtype=float)).fillna(0).astype(float) > 0).sum()))
    dns_failure_baseline = _baseline_stats(historical_dns_failures)
    if latest_dns_failures >= max(3, dns_failure_baseline["p95"] + 1):
        score = min(100.0, latest_dns_failures * 16.0 + dns_failure_baseline["p95"] * 5.0)
        alerts.append(
            {
                "device_id": device_id,
                "bucket_start": bucket_start,
                "alert_type": "dns_failure_spike",
                "severity": _behavior_severity(score, 75.0),
                "score": round(score, 2),
                "title": "DNS failure spike",
                "description": f"Device generated {latest_dns_failures} DNS failures in the latest bucket.",
                "evidence": {
                    "dns_failures": latest_dns_failures,
                    "baseline_dns_failures_median": round(dns_failure_baseline["median"], 2),
                    "baseline_dns_failures_p95": round(dns_failure_baseline["p95"], 2),
                },
            }
        )

    latest_nxdomain_failures = int((latest_bucket.get("dns_rcode", pd.Series(dtype=float)).fillna(0).astype(float) == 3).sum())
    historical_nxdomain_failures = []
    if _history_by_bucket and "dns_rcode" in history_flows.columns:
        for grp in _history_by_bucket.values():
            historical_nxdomain_failures.append(float((grp.get("dns_rcode", pd.Series(dtype=float)).fillna(0).astype(float) == 3).sum()))
    nxdomain_baseline = _baseline_stats(historical_nxdomain_failures)
    if latest_nxdomain_failures >= max(3, nxdomain_baseline["p95"] + 1):
        score = min(100.0, latest_nxdomain_failures * 18.0 + nxdomain_baseline["p95"] * 4.0)
        alerts.append(
            {
                "device_id": device_id,
                "bucket_start": bucket_start,
                "alert_type": "dns_nxdomain_burst",
                "severity": _behavior_severity(score, 78.0),
                "score": round(score, 2),
                "title": "NXDOMAIN burst detected",
                "description": f"Device generated {latest_nxdomain_failures} NXDOMAIN responses in the latest bucket.",
                "evidence": {
                    "nxdomain_failures": latest_nxdomain_failures,
                    "baseline_nxdomain_median": round(nxdomain_baseline["median"], 2),
                    "baseline_nxdomain_p95": round(nxdomain_baseline["p95"], 2),
                },
            }
        )

    latest_icmp_requests = latest_bucket[
        latest_bucket.get("icmp_type", pd.Series(dtype=float)).fillna(-1).astype(float) == 8
    ] if "icmp_type" in latest_bucket.columns else pd.DataFrame()
    latest_icmp_request_count = int(len(latest_icmp_requests))
    latest_icmp_destinations = int(latest_icmp_requests["dst_ip"].nunique()) if not latest_icmp_requests.empty else 0
    historical_icmp_counts = []
    if _history_by_bucket and "icmp_type" in history_flows.columns:
        for grp in _history_by_bucket.values():
            icmp_requests = grp[grp.get("icmp_type", pd.Series(dtype=float)).fillna(-1).astype(float) == 8]
            historical_icmp_counts.append(float(len(icmp_requests)))
    icmp_baseline = _baseline_stats(historical_icmp_counts)
    if latest_icmp_request_count >= max(4, icmp_baseline["p95"] + 1) and latest_icmp_destinations >= 3:
        score = min(100.0, latest_icmp_request_count * 10.0 + latest_icmp_destinations * 8.0)
        alerts.append(
            {
                "device_id": device_id,
                "bucket_start": bucket_start,
                "alert_type": "icmp_sweep_suspected",
                "severity": _behavior_severity(score, 78.0),
                "score": round(score, 2),
                "title": "ICMP sweep suspected",
                "description": f"Device sent {latest_icmp_request_count} ICMP echo requests to {latest_icmp_destinations} destinations in the latest bucket.",
                "evidence": {
                    "icmp_echo_requests": latest_icmp_request_count,
                    "unique_icmp_destinations": latest_icmp_destinations,
                    "baseline_icmp_requests_median": round(icmp_baseline["median"], 2),
                    "baseline_icmp_requests_p95": round(icmp_baseline["p95"], 2),
                },
            }
        )

    icmp_fanout_ratio = latest_icmp_destinations / max(latest_icmp_request_count, 1)
    if latest_icmp_request_count >= 3 and latest_icmp_destinations >= 4 and icmp_fanout_ratio >= 0.75:
        score = min(100.0, latest_icmp_destinations * 11.0 + icmp_fanout_ratio * 20.0)
        alerts.append(
            {
                "device_id": device_id,
                "bucket_start": bucket_start,
                "alert_type": "icmp_echo_fanout",
                "severity": _behavior_severity(score, 76.0),
                "score": round(score, 2),
                "title": "ICMP echo fanout",
                "description": f"Device spread ICMP echo requests across {latest_icmp_destinations} destinations in the latest bucket.",
                "evidence": {
                    "icmp_echo_requests": latest_icmp_request_count,
                    "unique_icmp_destinations": latest_icmp_destinations,
                    "fanout_ratio": round(icmp_fanout_ratio, 4),
                },
            }
        )

    return alerts


async def run_inference_once(detector: AnomalyDetector, hours: int):
    # Load flows once (baseline_hours >= hours, so we load the larger window)
    baseline_hours = int(os.getenv("BEHAVIOR_BASELINE_HOURS", "168"))
    all_flows = await get_all_recent_flows(hours=max(hours, baseline_hours))
    
    if all_flows.empty:
        log.info("inference_no_data")
        return 0

    # Filter flows for feature extraction (recent window)
    cutoff = pd.Timestamp.now() - pd.Timedelta(hours=hours)
    flows = all_flows[all_flows["timestamp"] >= cutoff]

    extractor = FeatureExtractor()
    features = _cached_extract(extractor, flows, "inference:recent")
    
    # Prepare baseline flows with bucket_start column
    baseline_flows = all_flows
    if not baseline_flows.empty:
        baseline_flows["bucket_start"] = baseline_flows["timestamp"].dt.floor(f"{extractor.bucket_minutes}min")
    
    baseline_features = _cached_extract(extractor, baseline_flows, "inference:baseline")
    per_device_models = os.getenv("PER_DEVICE_MODELS", "true").lower() == "true"
    default_model_type = os.getenv("MODEL_TYPE", "isolation_forest")

    # Load per-device model type configs (determines which model drives risk_score)
    device_model_configs = await get_device_model_configs()

    # Score ALL models per device, collect results for device_model_scores table
    all_model_scores: list[dict] = []
    # Active model scored results (for risk/anomalies/history)
    scored_results = []

    if per_device_models:
        for device_id, group in features.groupby('device_id'):
            latest = group.sort_values('bucket_start').tail(1)
            active_model_type = device_model_configs.get(int(device_id), default_model_type)
            bucket_start = latest.iloc[0].get('bucket_start') if not latest.empty else None

            # Score ALL available model types for this device
            for model_type in AVAILABLE_MODEL_TYPES:
                device_detector = get_detector(model_type, model_path=os.getenv("MODEL_PATH", "/data/models"))
                if not device_detector.load_model(device_id=int(device_id)):
                    # For active model, also try legacy fallback
                    if model_type == active_model_type:
                        legacy_detector = AnomalyDetector(model_path=os.getenv("MODEL_PATH", "/data/models"))
                        if not legacy_detector.load_model(device_id=int(device_id)):
                            log.warning("inference_model_missing_for_device", device_id=int(device_id), model_type=model_type)
                            continue
                        device_detector = legacy_detector
                    else:
                        continue  # Non-active model not yet trained, skip

                rows = device_detector.score(latest)
                for row in rows:
                    # Use z-score-normalised values for risk so all model types share a
                    # common scale; fall back to raw score when score_stats are absent
                    # (e.g. old-format models loaded via backward-compat path).
                    norm_s = device_detector.normalize_score(row["anomaly_score"])
                    norm_t = device_detector.normalize_threshold()
                    ml_risk = _risk_from_score(norm_s, norm_t)
                    all_model_scores.append({
                        "device_id": int(device_id),
                        "model_type": model_type,
                        "bucket_start": bucket_start,
                        "anomaly_score": float(row["anomaly_score"]),
                        "risk_score": float(ml_risk),
                        "is_anomaly": bool(row["is_anomaly"]),
                    })

                    # If this is the active model, add to scored_results for risk pipeline.
                    # Keep raw threshold for observability; pass normalised values separately.
                    if model_type == active_model_type:
                        scored_results.append(
                            {
                                **row,
                                "threshold": device_detector.threshold,
                                "norm_score": norm_s,
                                "norm_threshold": norm_t,
                            }
                        )
    else:
        scored_results = [
            {
                **row,
                "threshold": detector.threshold,
                "norm_score": detector.normalize_score(row["anomaly_score"]),
                "norm_threshold": detector.normalize_threshold(),
            }
            for row in detector.score(features)
        ]

    # Save all model scores to device_model_scores table
    await batch_save_model_scores(all_model_scores)

    # Pre-group baseline data per device to avoid O(N*D) filtering inside the loop
    baseline_flows_by_device: dict = {}
    baseline_features_by_device: dict = {}
    if not baseline_flows.empty:
        for did, grp in baseline_flows.groupby("device_id"):
            baseline_flows_by_device[did] = grp
    if not baseline_features.empty:
        for did, grp in baseline_features.groupby("device_id"):
            baseline_features_by_device[did] = grp

    anomaly_count = 0
    batch_results = []
    for a in scored_results:
        device_id = a["device_id"]
        score = a["anomaly_score"]
        is_anomaly = bool(a.get("is_anomaly"))
        severity = a["severity"]
        threshold = float(a.get("threshold", os.getenv("ANOMALY_THRESHOLD", "-0.5")))
        # Prefer normalised (z-score) values so the risk function operates on a
        # unified scale regardless of which model type produced the score.
        # Falls back to raw score/threshold when normalisation stats are unavailable.
        norm_score = float(a.get("norm_score", score))
        norm_threshold = float(a.get("norm_threshold", threshold))
        bucket_start = a.get("bucket_start")

        dev_flows = baseline_flows_by_device.get(device_id, pd.DataFrame())
        dev_features = baseline_features_by_device.get(device_id, pd.DataFrame())

        latest_bucket_flows = dev_flows[
            dev_flows["bucket_start"] == bucket_start
        ] if bucket_start is not None and not dev_flows.empty else pd.DataFrame()
        history_bucket_features = dev_features[
            dev_features["bucket_start"] < bucket_start
        ] if bucket_start is not None and not dev_features.empty else pd.DataFrame()
        history_flows = dev_flows[
            dev_flows["bucket_start"] < bucket_start
        ] if bucket_start is not None and not dev_flows.empty else pd.DataFrame()

        behavior_alerts = _build_behavior_alerts(device_id, latest_bucket_flows, history_bucket_features, history_flows)
        ml_risk = _risk_from_score(norm_score, norm_threshold)
        risk_breakdown = _risk_with_contributors(ml_risk, behavior_alerts)
        risk_score = risk_breakdown["final_risk"]
        inference_features = {
            **(a.get("features") or {}),
            "threshold": threshold,
            "norm_score": norm_score,
            "norm_threshold": norm_threshold,
            "ml_risk": risk_breakdown["ml_risk"],
            "behavior_risk": risk_breakdown["behavior_risk"],
            "protocol_risk": risk_breakdown["protocol_risk"],
            "correlation_bonus": risk_breakdown["correlation_bonus"],
            "risk_top_reason": risk_breakdown["top_reason"],
            "risk_reason_summary": risk_breakdown["reason_summary"],
        }

        log.info(
            "inference_device_score",
            device_id=device_id,
            score=float(score),
            norm_score=norm_score,
            norm_threshold=norm_threshold,
            ml_risk=ml_risk,
            behavior_risk=risk_breakdown["behavior_risk"],
            protocol_risk=risk_breakdown["protocol_risk"],
            correlation_bonus=risk_breakdown["correlation_bonus"],
            risk_score=risk_score,
            threshold=threshold,
            behavior_alert_count=len(behavior_alerts),
            top_reason=risk_breakdown["top_reason"],
            is_anomaly=is_anomaly,
        )

        if is_anomaly:
            anomaly_count += 1

        batch_results.append({
            "device_id": device_id,
            "bucket_start": bucket_start,
            "anomaly_score": float(score),
            "risk_score": risk_score,
            "is_anomaly": is_anomaly,
            "severity": severity,
            "features": inference_features,
            "behavior_alerts": behavior_alerts,
            "is_isolation_forest_anomaly": is_anomaly,
            "raw_features": a.get("features") or {},
        })

    # Write all results in a single DB connection (eliminates N+1 connect/close)
    await batch_save_inference_cycle(batch_results, retention_days=7)

    log.info(
        "inference_complete",
        at=datetime.now(UTC).isoformat(),
        devices=int(features.shape[0]),
        anomalies=anomaly_count,
    )

    return anomaly_count


async def run_retention_cleanup(
    flows_days: int = 7,
    alerts_days: int = 14,
    anomaly_resolve_hours: int = 48,
    batch_size: int = 5000,
) -> None:
    """
    Delete old rows and auto-resolve stale anomalies to keep the DB lean.

    Deletes are done in batches of `batch_size` rows so the write lock is
    released frequently, preventing the collector from hitting busy_timeout
    when there are millions of flows to prune.
    """
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA busy_timeout=5000")

            # ── traffic_flows: batch delete ──────────────────────────────
            deleted_flows = 0
            while True:
                cur = await conn.execute(
                    """DELETE FROM traffic_flows WHERE rowid IN (
                           SELECT rowid FROM traffic_flows
                           WHERE timestamp < datetime('now', ?)
                           LIMIT ?
                       )""",
                    (f"-{flows_days} days", batch_size),
                )
                deleted_flows += cur.rowcount
                await conn.commit()
                if cur.rowcount < batch_size:
                    break  # No more rows to delete

            # ── device_behavior_alerts: batch delete ─────────────────────
            deleted_alerts = 0
            while True:
                cur = await conn.execute(
                    """DELETE FROM device_behavior_alerts WHERE rowid IN (
                           SELECT rowid FROM device_behavior_alerts
                           WHERE timestamp < datetime('now', ?)
                           LIMIT ?
                       )""",
                    (f"-{alerts_days} days", batch_size),
                )
                deleted_alerts += cur.rowcount
                await conn.commit()
                if cur.rowcount < batch_size:
                    break

            # ── anomalies: auto-resolve stale open anomalies ─────────────
            cur = await conn.execute(
                """UPDATE anomalies SET resolved = 1
                   WHERE resolved = 0
                     AND timestamp < datetime('now', ?)""",
                (f"-{anomaly_resolve_hours} hours",),
            )
            resolved = cur.rowcount
            await conn.commit()

        log.info(
            "retention_cleanup",
            deleted_flows=deleted_flows,
            deleted_alerts=deleted_alerts,
            auto_resolved_anomalies=resolved,
        )
    except Exception as exc:
        log.error("retention_cleanup_error", error=str(exc))


async def run_inference_loop():
    interval = int(os.getenv("INFERENCE_INTERVAL", "300"))
    hours = int(os.getenv("INFERENCE_HOURS", "24"))
    per_device_models = os.getenv("PER_DEVICE_MODELS", "true").lower() == "true"
    heartbeat_path = os.getenv("HEARTBEAT_PATH", "/tmp/inference-heartbeat")

    detector = AnomalyDetector(model_path=os.getenv("MODEL_PATH", "/data/models"))

    # Run schema migrations once at startup, not per-cycle
    await ensure_schema()

    while True:
        try:
            if not per_device_models and detector.model is None:
                detector.load_model()
            if not per_device_models and detector.model is None:
                log.warning("inference_model_missing")
            else:
                await run_inference_once(detector, hours=hours)
            # Write heartbeat after a successful cycle.
            with open(heartbeat_path, "w") as f:
                f.write(str(int(__import__("time").time())))
            # Retention: prune old rows once per cycle
            await run_retention_cleanup()
        except Exception as e:
            log.error("inference_error", error=str(e))

        await asyncio.sleep(interval)


def main():
    asyncio.run(run_inference_loop())


if __name__ == "__main__":
    main()
