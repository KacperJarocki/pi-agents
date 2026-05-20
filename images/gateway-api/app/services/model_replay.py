import math
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


MODEL_TYPES = ["isolation_forest", "lof", "ocsvm", "autoencoder"]
FEATURE_COLUMNS = [
    "total_bytes", "packets", "unique_destinations", "unique_ports",
    "dns_queries", "avg_bytes_per_packet", "packet_rate", "connection_duration_avg",
    "protocol_entropy", "dst_ip_entropy", "dns_to_total_ratio", "iat_std",
]
_ARTIFACT_CACHE: dict[str, tuple[float, tuple[object, float, dict, int]]] = {}


def _risk_from_score(score: float, threshold: float) -> float:
    margin = threshold - score
    if margin <= 0:
        window = max(abs(threshold), 0.5)
        fraction = max(0.0, 1.0 + margin / window)
        return round(max(0.0, min(35.0, 35.0 * fraction)), 4)

    threshold_scale = max(abs(threshold), 0.05)
    normalized = margin / threshold_scale
    risk = 35.0 + min(65.0, normalized * 45.0)
    return round(max(0.0, min(100.0, risk)), 4)


def _entropy(series: pd.Series) -> float:
    counts = series.value_counts(normalize=True)
    if len(counts) <= 1:
        return 0.0
    return float(-sum(p * math.log2(p) for p in counts if p > 0))


def _extract_features(flows: pd.DataFrame, bucket_minutes: int) -> pd.DataFrame:
    if flows.empty:
        return pd.DataFrame(columns=["device_id", "bucket_start", *FEATURE_COLUMNS])

    rows = []
    with_bucket = flows.assign(bucket_start=flows["timestamp"].dt.floor(f"{bucket_minutes}min"))
    for (device_id, bucket_start), group in with_bucket.groupby(["device_id", "bucket_start"]):
        group = group.sort_values("timestamp")
        packets = len(group)
        total_bytes = float(group["bytes_sent"].sum() + group.get("bytes_received", pd.Series([0] * packets)).sum())
        dns_queries = int(group["dns_query"].notna().sum())
        iats = group["timestamp"].diff().dropna().dt.total_seconds()
        time_span = (group["timestamp"].max() - group["timestamp"].min()).total_seconds()
        rows.append({
            "device_id": int(device_id),
            "bucket_start": bucket_start,
            "total_bytes": total_bytes,
            "packets": packets,
            "unique_destinations": int(group["dst_ip"].nunique()),
            "unique_ports": int(group["dst_port"].nunique()),
            "dns_queries": dns_queries,
            "avg_bytes_per_packet": total_bytes / packets if packets else 0.0,
            "packet_rate": packets / time_span if time_span > 0 else 0.0,
            "connection_duration_avg": float(iats.mean()) if len(iats) > 0 else 0.0,
            "protocol_entropy": _entropy(group["protocol"]) if "protocol" in group else 0.0,
            "dst_ip_entropy": _entropy(group["dst_ip"].dropna()) if not group["dst_ip"].dropna().empty else 0.0,
            "dns_to_total_ratio": dns_queries / packets if packets else 0.0,
            "iat_std": float(iats.std()) if len(iats) > 1 else 0.0,
        })
    return pd.DataFrame(rows)


def _current_model_path(model_root: str, device_id: int, model_type: str) -> str:
    if model_type == "isolation_forest":
        name = f"isolation_forest_model_device_{device_id}.joblib"
    else:
        name = f"{model_type}_model_device_{device_id}.joblib"
    return str(Path(model_root) / name)


def _load_artifact(path: str) -> tuple[object, float, dict, int]:
    mtime = os.path.getmtime(path)
    cached = _ARTIFACT_CACHE.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    raw = joblib.load(path)
    payload = raw if isinstance(raw, dict) and "model" in raw else {"model": raw}
    model = payload["model"]
    threshold = float(payload.get("threshold", os.getenv("ANOMALY_THRESHOLD", "-0.5")))
    score_stats = payload.get("score_stats", {}) or {}
    features_count = int(payload.get("features_count") or _infer_features_count(model) or len(FEATURE_COLUMNS))
    loaded = (model, threshold, score_stats, features_count)
    _ARTIFACT_CACHE[path] = (mtime, loaded)
    return loaded


def _infer_features_count(model: object) -> int | None:
    candidate = model
    if isinstance(candidate, dict):
        candidate = candidate.get("lof") or candidate.get("mlp") or candidate.get("svm") or next(
            (v for v in candidate.values() if hasattr(v, "n_features_in_")), None
        )
    value = getattr(candidate, "n_features_in_", None)
    return int(value) if value is not None else None


def _decision_scores(model_type: str, model: object, x: np.ndarray) -> np.ndarray:
    if model_type == "isolation_forest":
        return model.decision_function(x)
    if model_type == "lof":
        lof = model["lof"] if isinstance(model, dict) else model
        x_scaled = model["scaler"].transform(x) if isinstance(model, dict) and "scaler" in model else x
        return lof.decision_function(x_scaled)
    if model_type == "ocsvm":
        return model["svm"].decision_function(model["scaler"].transform(x))
    if model_type == "autoencoder":
        scaler = model["scaler"]
        x_scaled = scaler.transform(x)
        reconstructed = model["mlp"].predict(x_scaled)
        errors = np.mean((x_scaled - reconstructed) ** 2, axis=1)
        stats = model["error_stats"]
        return -(errors - stats["mean"]) / max(stats["std"], 1e-8)
    raise ValueError(f"unsupported model_type: {model_type}")


def _normalize(raw_score: float, stats: dict) -> float:
    mean = stats.get("mean")
    std = stats.get("std")
    if mean is None or std is None:
        return raw_score
    if float(std) < 1e-8:
        deviation = raw_score - float(mean)
        if abs(deviation) < 1e-8:
            return 0.0
        return -10.0 if deviation < 0 else 10.0
    return (raw_score - float(mean)) / float(std)


class ModelReplayService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.model_root = os.getenv("MODEL_PATH", "/data/models")
        self.bucket_minutes = int(os.getenv("FEATURE_BUCKET_MINUTES", "5"))

    async def replay(self, device_id: int, model_type: str, hours: int, model_registry_id: int | None = None) -> dict:
        flows = await self._flows(device_id, hours)
        features = _extract_features(flows, self.bucket_minutes)
        if model_type == "all" and model_registry_id is None:
            results = []
            for mt in MODEL_TYPES:
                artifact = await self._artifact(device_id, mt, None)
                try:
                    results.append(self._score_artifact(device_id, hours, flows, features, artifact))
                except FileNotFoundError:
                    continue
            return {
                "device_id": device_id,
                "model_type": "all",
                "hours": hours,
                "bucket_minutes": self.bucket_minutes,
                "flow_count": int(len(flows)),
                "bucket_count": int(len(features)),
                "results": results,
            }

        artifact = await self._artifact(device_id, model_type, model_registry_id)
        return self._score_artifact(device_id, hours, flows, features, artifact)

    def _score_artifact(self, device_id: int, hours: int, flows: pd.DataFrame, features: pd.DataFrame, artifact: dict) -> dict:
        if not Path(artifact["model_path"]).exists():
            raise FileNotFoundError(artifact["model_path"])
        model, threshold, score_stats, features_count = _load_artifact(artifact["model_path"])

        rows = []
        if not features.empty:
            cols = FEATURE_COLUMNS[:features_count]
            scores = _decision_scores(artifact["model_type"], model, features[cols].values)
            norm_threshold = _normalize(threshold, score_stats)
            for idx, raw_score in enumerate(scores):
                norm_score = _normalize(float(raw_score), score_stats)
                rows.append({
                    "timestamp": features.iloc[idx]["bucket_start"].isoformat(),
                    "bucket_start": features.iloc[idx]["bucket_start"].isoformat(),
                    "anomaly_score": float(raw_score),
                    "normalized_score": float(norm_score),
                    "threshold": float(threshold),
                    "normalized_threshold": float(norm_threshold),
                    "risk_score": _risk_from_score(norm_score, norm_threshold),
                    "is_anomaly": bool(float(raw_score) < threshold),
                    "features": features.iloc[idx][cols].to_dict(),
                })

        return {
            "device_id": device_id,
            "model_type": artifact["model_type"],
            "model_registry_id": artifact.get("model_registry_id"),
            "model_path": artifact["model_path"],
            "hours": hours,
            "bucket_minutes": self.bucket_minutes,
            "flow_count": int(len(flows)),
            "bucket_count": int(len(features)),
            "data": rows,
        }

    async def _artifact(self, device_id: int, model_type: str, model_registry_id: int | None) -> dict:
        if model_registry_id is not None:
            row = (await self.db.execute(text("""
                SELECT id, model_type, model_path FROM model_registry
                WHERE id = :id AND device_id = :did
            """), {"id": model_registry_id, "did": device_id})).first()
            if row is None:
                raise FileNotFoundError(f"model_registry id not found: {model_registry_id}")
            return {"model_registry_id": int(row.id), "model_type": row.model_type, "model_path": row.model_path}
        return {"model_registry_id": None, "model_type": model_type, "model_path": _current_model_path(self.model_root, device_id, model_type)}

    async def _flows(self, device_id: int, hours: int) -> pd.DataFrame:
        rows = (await self.db.execute(text("""
            SELECT device_id, timestamp, src_ip, dst_ip, src_port, dst_port,
                   protocol, bytes_sent, bytes_received, dns_query
            FROM traffic_flows
            WHERE device_id = :did AND datetime(timestamp) >= datetime('now', :since)
            ORDER BY timestamp ASC
            LIMIT 50000
        """), {"did": device_id, "since": f"-{hours} hours"})).mappings().all()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(row) for row in rows])
        df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
        return df
