"""
ml_core.py — core ML utilities for the IoT anomaly detection pipeline.

Architecture
------------
* **FeatureExtractor** — aggregates raw traffic_flows into 5-minute feature buckets
  (8 numeric features per device per bucket).

* **BaseDetector** (ABC) — shared interface for all anomaly detectors.
  Key responsibilities:
    - ``fit(X)`` — train model on feature matrix; every subclass MUST call
      ``_compute_and_store_score_stats(scores, contamination_rate)`` at the end
      so that the adaptive threshold and score-distribution stats are persisted.
    - ``save_model(model, device_id)`` — serialises ``{"model", "threshold",
      "score_stats"}`` via joblib (new format) instead of the raw model object.
    - ``load_model(device_id)`` — handles both the new dict format and the
      legacy raw-model format for backward compatibility.
    - ``normalize_score(raw)`` / ``normalize_threshold()`` — map raw decision
      scores to a common z-score space so that IF, LOF, OCSVM, and Autoencoder
      scores become directly comparable.
    - ``score(features)`` / ``detect(features)`` — produce per-bucket anomaly
      annotations using the per-model adaptive threshold.

* **Adaptive threshold (Faza A)** — each detector computes its threshold from
  the training data at the contamination percentile:
      threshold = np.percentile(decision_scores(X_train), contamination * 100)
  This replaces the single global ``ANOMALY_THRESHOLD`` env-var default (-0.5)
  which was too coarse for LOF / OCSVM / Autoencoder score scales.
  The env-var is kept as a fallback for old model files that predate this change.

* **Score normalisation (Faza B)** — ``normalize_score()`` maps raw scores to
  z-scores using the training distribution:
      z = (raw - mean) / std
  This lets ``_risk_from_score()`` in inference.py operate on a unified scale
  regardless of which detector is active.

* **ML observability (Faza C)** — training metadata (threshold, score
  distribution, estimated anomaly rate) is persisted to the ``model_metadata``
  SQLite table via ``save_model_training_metadata()``, queried by the gateway-api
  ``/api/v1/metrics/ml-status`` endpoint.

Database tables managed here
-----------------------------
* device_inference_history
* device_behavior_alerts
* device_model_config
* device_model_scores
* model_metadata  ← new, Faza C
"""

import os
import structlog
import aiosqlite
import pandas as pd
import numpy as np
import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()

DB_PATH = os.getenv("DATABASE_PATH", "/data/iot-security.db")
MODEL_DIR = os.getenv("MODEL_PATH", "/data/models")


# ──────────────────────────────────────────────────────────────────────────────
# Feature extraction
# ──────────────────────────────────────────────────────────────────────────────

class FeatureExtractor:
    """Aggregate raw traffic_flows rows into fixed-width time buckets.

    Each (device_id, bucket_start) pair produces one row with 12 numeric
    features.  The bucket width defaults to ``FEATURE_BUCKET_MINUTES`` env-var
    (default 5 minutes).
    """

    # First 8 columns are the original feature set (backward-compat with old models).
    # New columns appended at the end so old models can use features[:8].
    FEATURE_COLUMNS = [
        'total_bytes', 'packets', 'unique_destinations',
        'unique_ports', 'dns_queries', 'avg_bytes_per_packet',
        'packet_rate', 'connection_duration_avg',
        # New in Faza 9:
        'protocol_entropy',    # Shannon entropy of protocol distribution (TCP/UDP/ICMP/…)
        'dst_ip_entropy',      # Shannon entropy of destination IP distribution
        'dns_to_total_ratio',  # dns_queries / packets (0-1; high = DNS tunneling)
        'iat_std',             # Std-dev of inter-arrival times (low = beaconing)
    ]

    @staticmethod
    def _shannon_entropy(series: "pd.Series") -> float:
        """Compute normalised Shannon entropy (0=uniform single value, 1=max diversity)."""
        counts = series.value_counts(normalize=True)
        if len(counts) <= 1:
            return 0.0
        import math
        return float(-sum(p * math.log2(p) for p in counts if p > 0))

    def __init__(self, bucket_minutes: int | None = None):
        self.bucket_minutes = bucket_minutes or int(os.getenv("FEATURE_BUCKET_MINUTES", "5"))

    def extract_features(self, flows: pd.DataFrame) -> pd.DataFrame:
        """Return a DataFrame with one row per (device_id, bucket_start)."""
        if flows.empty:
            return pd.DataFrame(columns=['device_id', 'bucket_start', *self.FEATURE_COLUMNS])
        
        features = []
        bucket = f"{self.bucket_minutes}min"
        # assign() avoids mutating the caller's DataFrame
        flows_with_bucket = flows.assign(bucket_start=flows['timestamp'].dt.floor(bucket))
        
        for (device_id, bucket_start), group in flows_with_bucket.groupby(['device_id', 'bucket_start']):
            device_flows = group.sort_values('timestamp')
            
            packets = len(device_flows)
            bytes_col = device_flows['bytes_sent'].sum() + device_flows.get('bytes_received', pd.Series([0]*packets)).sum() if 'bytes_received' in device_flows.columns else device_flows['bytes_sent'].sum()
            total_bytes = float(bytes_col)
            unique_destinations = device_flows['dst_ip'].nunique()
            unique_ports = device_flows['dst_port'].nunique()
            dns_queries = int(device_flows['dns_query'].notna().sum())

            avg_bytes_per_packet = total_bytes / packets if packets > 0 else 0.0
            
            time_span = (device_flows['timestamp'].max() - device_flows['timestamp'].min()).total_seconds()
            packet_rate = packets / time_span if time_span > 0 else 0.0

            iats = device_flows['timestamp'].diff().dropna().dt.total_seconds()
            connection_duration_avg = float(iats.mean()) if len(iats) > 0 else 0.0
            iat_std = float(iats.std()) if len(iats) > 1 else 0.0

            # New features
            protocol_entropy = self._shannon_entropy(device_flows['protocol']) if 'protocol' in device_flows.columns else 0.0
            dst_ip_entropy = self._shannon_entropy(device_flows['dst_ip'].dropna()) if not device_flows['dst_ip'].dropna().empty else 0.0
            dns_to_total_ratio = dns_queries / packets if packets > 0 else 0.0

            features.append({
                'device_id': device_id,
                'bucket_start': bucket_start,
                'total_bytes': total_bytes,
                'packets': packets,
                'unique_destinations': unique_destinations,
                'unique_ports': unique_ports,
                'dns_queries': dns_queries,
                'avg_bytes_per_packet': avg_bytes_per_packet,
                'packet_rate': packet_rate,
                'connection_duration_avg': connection_duration_avg,
                'protocol_entropy': protocol_entropy,
                'dst_ip_entropy': dst_ip_entropy,
                'dns_to_total_ratio': dns_to_total_ratio,
                'iat_std': iat_std,
            })
        
        return pd.DataFrame(features)


# ──────────────────────────────────────────────────────────────────────────────
# BaseDetector — adaptive threshold + score normalisation
# ──────────────────────────────────────────────────────────────────────────────

class BaseDetector(ABC):
    """Abstract base for all anomaly detectors.

    Subclasses implement ``fit()`` and ``decision_scores()``.
    Every ``fit()`` implementation MUST finish by calling
    ``_compute_and_store_score_stats(training_scores, contamination_rate)``
    so that adaptive threshold and score distribution stats are stored and
    later persisted alongside the model file.

    Saved model format (new, Faza A+B)
    ------------------------------------
    ``{"model": <sklearn model or dict>, "threshold": float,
       "score_stats": {"mean", "std", "min", "max", "p5", "p50", "p95"}}``

    Old format (raw sklearn object) is handled transparently in ``load_model``.
    The global ``ANOMALY_THRESHOLD`` env-var is used as a fallback for old files.
    """

    MODEL_TYPE: str = "base"
    # Class-level cache: {(MODEL_TYPE, device_id): (mtime, payload_dict)}
    _model_cache: dict = {}

    def __init__(self, model_path: str):
        self.model_path = model_path
        self.model = None
        # Adaptive threshold — overwritten by load_model() / _compute_and_store_score_stats()
        self.threshold = float(os.getenv("ANOMALY_THRESHOLD", "-0.5"))
        # Score distribution from training data (set by _compute_and_store_score_stats)
        self._score_stats: dict = {}
        # Number of features model was trained on (set by _apply_payload, used for compat)
        self._features_count: int = len(FeatureExtractor.FEATURE_COLUMNS)

    # ── file path helpers ────────────────────────────────────────────────────

    def _model_file(self, device_id: int | None = None) -> str:
        name = (
            f"{self.MODEL_TYPE}_model_device_{device_id}.joblib"
            if device_id is not None
            else f"{self.MODEL_TYPE}_model.joblib"
        )
        return os.path.join(self.model_path, name)

    def model_exists(self, device_id: int | None = None) -> bool:
        return os.path.exists(self._model_file(device_id))

    # ── persistence ──────────────────────────────────────────────────────────

    def load_model(self, device_id: int | None = None) -> bool:
        """Load model from disk with mtime-keyed in-memory cache.

        Handles both the new dict payload format and the legacy raw-model
        format so that old joblib files remain usable after upgrading.
        """
        import joblib
        model_file = self._model_file(device_id)
        if not os.path.exists(model_file):
            return False
        mtime = os.path.getmtime(model_file)
        cache_key = (self.MODEL_TYPE, device_id)
        cached = BaseDetector._model_cache.get(cache_key)
        if cached is not None and cached[0] == mtime:
            payload = cached[1]
            self._apply_payload(payload)
            return True
        try:
            raw = joblib.load(model_file)
        except Exception as exc:
            log.error("model_load_failed", path=model_file, device_id=device_id,
                      model_type=self.MODEL_TYPE, error=str(exc))
            return False
        # Detect new format vs legacy raw model
        if isinstance(raw, dict) and "model" in raw:
            payload = raw
        else:
            # Legacy: raw sklearn model object
            payload = {"model": raw}
        self._apply_payload(payload)
        BaseDetector._model_cache[cache_key] = (mtime, payload)
        log.info("model_loaded", path=model_file, device_id=device_id, model_type=self.MODEL_TYPE)
        return True

    def _apply_payload(self, payload: dict) -> None:
        """Extract model, threshold and score_stats from a payload dict."""
        self.model = payload.get("model") if isinstance(payload, dict) else payload
        if isinstance(payload, dict):
            self.threshold = float(payload.get("threshold", self.threshold))
            self._score_stats = payload.get("score_stats", {})
            # features_count allows scoring with the correct number of input columns
            # when models trained on old 8-column data are loaded by a 12-column extractor.
            if "features_count" in payload:
                self._features_count = int(payload["features_count"])
            else:
                # Old model saved without features_count — infer from the sklearn
                # model's n_features_in_ attribute so 8-feature legacy models are
                # not erroneously fed 12 columns.
                _m = payload.get("model") if isinstance(payload, dict) else payload
                # Dict-wrapped models (LOF, Autoencoder) store the sklearn object
                # under a sub-key.  Try common keys before falling back.
                if isinstance(_m, dict):
                    _m = _m.get("lof") or _m.get("mlp") or _m.get("svm") or next(
                        (v for v in _m.values() if hasattr(v, "n_features_in_")), _m
                    )
                _nf = getattr(_m, "n_features_in_", None)
                self._features_count = int(_nf) if _nf is not None else len(FeatureExtractor.FEATURE_COLUMNS)

    def save_model(self, model, device_id: int | None = None):
        """Persist model together with adaptive threshold and score stats.

        Saves a dict payload so that load_model() can restore the full
        distribution context without re-running training.
        """
        import joblib
        os.makedirs(self.model_path, exist_ok=True)
        model_file = self._model_file(device_id)
        payload = {
            "model": model,
            "threshold": self.threshold,
            "score_stats": self._score_stats,
            "features_count": len(FeatureExtractor.FEATURE_COLUMNS),
        }
        joblib.dump(payload, model_file)
        self.model = model
        log.info(
            "model_saved",
            path=model_file,
            device_id=device_id,
            model_type=self.MODEL_TYPE,
            threshold=round(self.threshold, 6),
            score_mean=round(self._score_stats.get("mean", 0.0), 6),
        )

    # ── adaptive threshold + score normalisation ─────────────────────────────

    def _compute_and_store_score_stats(
        self,
        scores: np.ndarray,
        contamination_rate: float,
    ) -> None:
        """Compute score distribution stats and set adaptive threshold.

        Called at the end of every subclass ``fit()`` implementation.

        The adaptive threshold is set to the ``contamination_rate``-th percentile
        of the training scores.  Because lower scores indicate more anomalous
        behaviour (IsolationForest convention), this places the threshold at the
        boundary below which we expect ~contamination_rate fraction of samples to
        fall — consistent across all four detector types.

        Parameters
        ----------
        scores : np.ndarray
            Decision scores for the training set (lower = more anomalous).
        contamination_rate : float
            Expected fraction of anomalies in the training data (e.g. 0.05).
        """
        self._score_stats = {
            "mean": float(np.mean(scores)),
            "std":  float(np.std(scores)),
            "min":  float(np.min(scores)),
            "max":  float(np.max(scores)),
            "p5":   float(np.percentile(scores, 5)),
            "p50":  float(np.percentile(scores, 50)),
            "p95":  float(np.percentile(scores, 95)),
        }
        # Place the threshold at the contamination percentile boundary.
        # E.g. contamination=0.05 → 5th percentile → bottom 5 % flagged.
        self.threshold = float(np.percentile(scores, contamination_rate * 100))

    def normalize_score(self, raw_score: float) -> float:
        """Map a raw decision score to a z-score using the training distribution.

        Returns ``(raw_score - mean) / std``.  A z-score of 0 corresponds to the
        average training score; large negative values indicate anomalies.

        Falls back to the raw score unchanged when no distribution stats are
        available (old-format model file loaded without score_stats).
        """
        mean = self._score_stats.get("mean", None)
        std  = self._score_stats.get("std",  None)
        if mean is None or std is None:
            # No distribution stats available (old-format model) — return raw score.
            return raw_score
        if std < 1e-8:
            # Degenerate distribution: all training scores were identical.
            # When the inference score deviates from that constant mean, it is
            # genuinely novel — return a large-magnitude z-score proportional to
            # the deviation so anomalies are not silently suppressed.
            deviation = raw_score - mean
            if abs(deviation) < 1e-8:
                return 0.0
            return -10.0 if deviation < 0 else 10.0
        return (raw_score - mean) / std

    def normalize_threshold(self) -> float:
        """Return the adaptive threshold expressed in the normalised z-score space."""
        return self.normalize_score(self.threshold)

    # ── inference helpers ────────────────────────────────────────────────────

    @abstractmethod
    def fit(self, X: np.ndarray, **kwargs):
        """Train the model on feature matrix X.

        Subclasses MUST call ``_compute_and_store_score_stats(scores, rate)``
        before returning.
        """

    @abstractmethod
    def decision_scores(self, X: np.ndarray) -> np.ndarray:
        """Return anomaly scores (lower = more anomalous, IF convention)."""

    def score(self, features: pd.DataFrame) -> List[Dict]:
        """Score feature rows; returns list of dicts with anomaly annotations."""
        if self.model is None or features.empty:
            return []
        # Use only the columns the model was trained on (backward compat: old 8-col models).
        n = getattr(self, "_features_count", len(FeatureExtractor.FEATURE_COLUMNS))
        cols = FeatureExtractor.FEATURE_COLUMNS[:n]
        X = features[cols].values
        scores = self.decision_scores(X)
        rows = []
        for idx, s in enumerate(scores):
            rows.append({
                'device_id': int(features.iloc[idx]['device_id']),
                'bucket_start': features.iloc[idx].get('bucket_start'),
                'anomaly_score': float(s),
                'is_anomaly': bool(s < self.threshold),
                # Critical when score is at least one threshold-width below the boundary.
                # Using (threshold - abs(threshold)) handles both negative and positive
                # thresholds: the critical zone is always a symmetric step below the cut.
                'severity': 'critical' if s < self.threshold - abs(self.threshold) else 'warning',
                'features': features.iloc[idx][cols].to_dict(),
            })
        return rows

    def detect(self, features: pd.DataFrame) -> List[Dict]:
        """Return only anomalous rows (score < threshold)."""
        if self.model is None or features.empty:
            return []
        n = getattr(self, "_features_count", len(FeatureExtractor.FEATURE_COLUMNS))
        cols = FeatureExtractor.FEATURE_COLUMNS[:n]
        X = features[cols].values
        scores = self.decision_scores(X)
        anomalies = []
        for idx, s in enumerate(scores):
            if s < self.threshold:
                anomalies.append({
                    'device_id': int(features.iloc[idx]['device_id']),
                    'anomaly_score': float(s),
                    'severity': 'critical' if s < self.threshold - abs(self.threshold) else 'warning',
                    'features': features.iloc[idx][cols].to_dict(),
                })
        return anomalies


# ──────────────────────────────────────────────────────────────────────────────
# Concrete detector implementations
# ──────────────────────────────────────────────────────────────────────────────

class IsolationForestDetector(BaseDetector):
    """Isolation Forest — ensemble of random trees.

    Threshold is set at the ``contamination``-th percentile of training
    decision_function scores (e.g. ~5th percentile for contamination=0.05).
    """

    MODEL_TYPE = "isolation_forest"

    def fit(self, X: np.ndarray, **kwargs):
        from sklearn.ensemble import IsolationForest
        contamination = kwargs.get("contamination", 0.05)
        n_estimators = kwargs.get("n_estimators", 200)
        warm_start = kwargs.get("warm_start", False)

        # warm_start=True reuses existing estimators and adds new trees on top,
        # enabling incremental training when n_estimators is increased between
        # runs.  Only effective when self.model is an existing IsolationForest.
        if warm_start and self.model is not None and hasattr(self.model, "n_estimators"):
            self.model.set_params(n_estimators=n_estimators, warm_start=True)
            self.model.fit(X)
            model = self.model
        else:
            model = IsolationForest(
                n_estimators=n_estimators,
                contamination=contamination,
                random_state=42,
                n_jobs=1,
            )
            model.fit(X)
        self.model = model
        # Adaptive threshold from training distribution
        training_scores = self.decision_scores(X)
        self._compute_and_store_score_stats(training_scores, contamination)
        return model

    def decision_scores(self, X: np.ndarray) -> np.ndarray:
        return self.model.decision_function(X)


class LOFDetector(BaseDetector):
    """Local Outlier Factor (novelty detection mode).

    n_neighbors is scaled to the training set size for small device histories.
    Threshold is computed from training decision_function scores.
    """

    MODEL_TYPE = "lof"

    def fit(self, X: np.ndarray, **kwargs):
        from sklearn.neighbors import LocalOutlierFactor
        from sklearn.preprocessing import StandardScaler
        contamination = kwargs.get("contamination", 0.05)
        n_neighbors = kwargs.get("n_neighbors", min(20, max(5, X.shape[0] // 5)))
        # Scale features so high-magnitude columns (total_bytes) don't dominate
        # the k-NN distance calculations used by LOF.
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        model = LocalOutlierFactor(
            n_neighbors=n_neighbors,
            contamination=contamination,
            novelty=True,
        )
        model.fit(X_scaled)
        self.model = {"lof": model, "scaler": scaler}
        training_scores = self.decision_scores(X)
        self._compute_and_store_score_stats(training_scores, contamination)
        return self.model

    def decision_scores(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("LOFDetector: model not loaded — call fit() or load_model() first")
        # Legacy models (pre-Faza 7) were saved as a raw LocalOutlierFactor
        # without a scaler wrapper.  Handle both formats gracefully.
        if isinstance(self.model, dict):
            scaler = self.model["scaler"]
            lof = self.model["lof"]
            X_scaled = scaler.transform(X)
        else:
            lof = self.model
            X_scaled = X
        return lof.decision_function(X_scaled)


class OneClassSVMDetector(BaseDetector):
    """One-Class SVM with RBF kernel, internally scales features.

    ``nu`` (upper bound on fraction of outliers) plays the same role as
    ``contamination``.  Threshold is derived from the training score
    distribution so the SVM boundary aligns with the risk pipeline.
    """

    MODEL_TYPE = "ocsvm"

    def fit(self, X: np.ndarray, **kwargs):
        from sklearn.svm import OneClassSVM
        from sklearn.preprocessing import StandardScaler
        nu = kwargs.get("nu", 0.05)
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        model = OneClassSVM(kernel='rbf', gamma='scale', nu=nu)
        model.fit(X_scaled)
        self.model = {"svm": model, "scaler": scaler}
        training_scores = self.decision_scores(X)
        # Use nu as the effective contamination rate
        self._compute_and_store_score_stats(training_scores, nu)
        return self.model

    def decision_scores(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("OneClassSVMDetector: model not loaded — call fit() or load_model() first")
        scaler = self.model["scaler"]
        svm = self.model["svm"]
        X_scaled = scaler.transform(X)
        return svm.decision_function(X_scaled)


class AutoencoderDetector(BaseDetector):
    """Autoencoder using sklearn MLPRegressor (no extra dependencies).

    Trained as an identity mapping (X → X).  Anomaly score is the negated
    z-score of reconstruction error: ``-(error - mean_error) / std_error``
    so that normal samples cluster near 0 and anomalies are strongly negative,
    consistent with the IsolationForest sign convention.

    Threshold and score_stats are derived from training z-scores via
    ``_compute_and_store_score_stats`` so the adaptive threshold typically
    lands around the contamination-percentile (e.g. ~-1.64 for 5 %).
    """

    MODEL_TYPE = "autoencoder"

    def fit(self, X: np.ndarray, **kwargs):
        from sklearn.neural_network import MLPRegressor
        from sklearn.preprocessing import StandardScaler
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        n_features = X.shape[1]
        hidden = max(4, n_features // 2)
        # early_stopping requires a non-empty validation split; disable it for
        # small training sets where validation_fraction would produce 0 samples.
        use_early_stopping = X.shape[0] >= 30
        model = MLPRegressor(
            hidden_layer_sizes=(hidden, max(4, hidden // 2), hidden),
            activation='relu',
            max_iter=kwargs.get("max_iter", 500),
            random_state=42,
            early_stopping=use_early_stopping,
            validation_fraction=0.1 if use_early_stopping else 0.0,
        )
        model.fit(X_scaled, X_scaled)
        # Compute reconstruction errors on training data for z-score baseline
        reconstructed = model.predict(X_scaled)
        errors = np.mean((X_scaled - reconstructed) ** 2, axis=1)
        error_mean = float(np.mean(errors))
        error_std  = float(np.std(errors))
        self.model = {
            "mlp": model,
            "scaler": scaler,
            "error_stats": {"mean": error_mean, "std": error_std},
        }
        # decision_scores already returns z-scores using error_stats
        training_scores = self.decision_scores(X)
        contamination = kwargs.get("contamination", 0.05)
        self._compute_and_store_score_stats(training_scores, contamination)
        return self.model

    def decision_scores(self, X: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise RuntimeError("AutoencoderDetector: model not loaded — call fit() or load_model() first")
        scaler = self.model["scaler"]
        mlp = self.model["mlp"]
        stats = self.model["error_stats"]
        X_scaled = scaler.transform(X)
        reconstructed = mlp.predict(X_scaled)
        errors = np.mean((X_scaled - reconstructed) ** 2, axis=1)
        # Negate so that lower score = more anomalous (IsolationForest convention)
        z_scores = -(errors - stats["mean"]) / max(stats["std"], 1e-8)
        return z_scores


# ──────────────────────────────────────────────────────────────────────────────
# Registry & factory
# ──────────────────────────────────────────────────────────────────────────────

DETECTOR_REGISTRY = {
    "isolation_forest": IsolationForestDetector,
    "lof": LOFDetector,
    "ocsvm": OneClassSVMDetector,
    "autoencoder": AutoencoderDetector,
}

AVAILABLE_MODEL_TYPES = list(DETECTOR_REGISTRY.keys())


def get_detector(model_type: str, model_path: str | None = None) -> BaseDetector:
    """Factory: return a detector instance for the given model type."""
    path = model_path or os.getenv("MODEL_PATH", "/data/models")
    cls = DETECTOR_REGISTRY.get(model_type)
    if cls is None:
        raise ValueError(f"Unknown model type '{model_type}'. Available: {AVAILABLE_MODEL_TYPES}")
    return cls(model_path=path)


class AnomalyDetector(IsolationForestDetector):
    """Backward-compatible alias for IsolationForestDetector.

    Keeps the old _model_file naming so that existing model files at
    ``isolation_forest_model_device_<id>.joblib`` remain loadable.
    """

    def _model_file(self, device_id: int | None = None) -> str:
        name = (
            f"isolation_forest_model_device_{device_id}.joblib"
            if device_id is not None
            else "isolation_forest_model.joblib"
        )
        return os.path.join(self.model_path, name)


# ──────────────────────────────────────────────────────────────────────────────
# Database I/O helpers
# ──────────────────────────────────────────────────────────────────────────────

async def get_device_flows(device_id: int, hours: int = 24) -> pd.DataFrame:
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        cursor = await conn.execute("""
            SELECT device_id, timestamp, src_ip, dst_ip, src_port, dst_port, 
                   protocol, bytes_sent, bytes_received, dns_query, flags
            FROM traffic_flows
            WHERE device_id = ? AND timestamp >= datetime('now', '-' || ? || ' hours')
            ORDER BY timestamp
        """, (device_id, hours))
        rows = await cursor.fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(row) for row in rows])
    df['timestamp'] = pd.to_datetime(df['timestamp'], format="mixed")
    if 'flags' in df.columns:
        df['flags'] = df['flags'].apply(lambda value: json.loads(value) if isinstance(value, str) and value else (value or {}))
        df['dns_rcode'] = df['flags'].apply(lambda value: value.get('dns_rcode') if isinstance(value, dict) else None)
        df['icmp_type'] = df['flags'].apply(lambda value: value.get('icmp_type') if isinstance(value, dict) else None)
        df['icmp_code'] = df['flags'].apply(lambda value: value.get('icmp_code') if isinstance(value, dict) else None)
    return df


_MAX_INFERENCE_FLOWS: int = int(os.getenv("MAX_INFERENCE_FLOWS", "500000"))


async def get_all_recent_flows(hours: int = 24) -> pd.DataFrame:
    row_limit = _MAX_INFERENCE_FLOWS
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        cursor = await conn.execute("""
            SELECT device_id, timestamp, src_ip, dst_ip, src_port, dst_port, 
                   protocol, bytes_sent, bytes_received, dns_query, flags
            FROM traffic_flows
            WHERE timestamp >= datetime('now', '-' || ? || ' hours')
            ORDER BY device_id, timestamp
            LIMIT ?
        """, (hours, row_limit))
        rows = await cursor.fetchall()
    if not rows:
        return pd.DataFrame()
    if len(rows) >= row_limit:
        log.warning("get_all_recent_flows_truncated", row_limit=row_limit, hours=hours)
    df = pd.DataFrame([dict(row) for row in rows])
    df['timestamp'] = pd.to_datetime(df['timestamp'], format="mixed")
    if 'flags' in df.columns:
        df['flags'] = df['flags'].apply(lambda value: json.loads(value) if isinstance(value, str) and value else (value or {}))
        df['dns_rcode'] = df['flags'].apply(lambda value: value.get('dns_rcode') if isinstance(value, dict) else None)
        df['icmp_type'] = df['flags'].apply(lambda value: value.get('icmp_type') if isinstance(value, dict) else None)
        df['icmp_code'] = df['flags'].apply(lambda value: value.get('icmp_code') if isinstance(value, dict) else None)
    return df


async def get_device_inference_history_features(hours: int = 168) -> pd.DataFrame:
    """Load aggregated feature buckets from device_inference_history.

    Returns a DataFrame with columns: device_id, bucket_start, total_bytes,
    packets, unique_destinations, unique_ports, dns_queries,
    avg_bytes_per_packet, packet_rate, connection_duration_avg.

    Used as the history baseline in run_inference_once so we avoid loading
    7 days of raw traffic_flows into memory on each inference cycle.
    """
    feature_cols = [
        'total_bytes', 'packets', 'unique_destinations', 'unique_ports',
        'dns_queries', 'avg_bytes_per_packet', 'packet_rate',
        'connection_duration_avg',
    ]
    async with aiosqlite.connect(DB_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
        cursor = await conn.execute("""
            SELECT device_id, bucket_start, features
            FROM device_inference_history
            WHERE timestamp >= datetime('now', '-' || ? || ' hours')
            ORDER BY device_id, bucket_start
        """, (hours,))
        rows = await cursor.fetchall()
    if not rows:
        return pd.DataFrame(columns=['device_id', 'bucket_start'] + feature_cols)
    records = []
    for row in rows:
        raw = json.loads(row['features']) if row['features'] else {}
        record = {
            'device_id': row['device_id'],
            'bucket_start': row['bucket_start'],
        }
        for col in feature_cols:
            record[col] = raw.get(col, 0.0)
        records.append(record)
    df = pd.DataFrame(records)
    df['bucket_start'] = pd.to_datetime(df['bucket_start'], format="mixed")
    return df


async def get_db_connection() -> aiosqlite.Connection:
    """Open a single SQLite connection with WAL mode. Caller must close."""
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    return conn


# ──────────────────────────────────────────────────────────────────────────────
# Schema migrations
# ──────────────────────────────────────────────────────────────────────────────

async def ensure_schema():
    """Run all schema migrations once at startup (not per-cycle)."""
    conn = await get_db_connection()
    try:
        await _ensure_device_inference_columns(conn)
        await _ensure_inference_history_table(conn)
        await _ensure_behavior_alerts_table(conn)
        await _ensure_device_model_config_table(conn)
        await _ensure_device_model_scores_table(conn)
        await _ensure_model_metadata_table(conn)
        await _ensure_training_config_tables(conn)
        await conn.commit()
        log.info("schema_ensured")
    finally:
        await conn.close()


async def _ensure_device_inference_columns(conn: aiosqlite.Connection):
    cursor = await conn.execute("PRAGMA table_info(devices)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "last_inference_score" not in cols:
        await conn.execute("ALTER TABLE devices ADD COLUMN last_inference_score REAL")
    if "last_inference_at" not in cols:
        await conn.execute("ALTER TABLE devices ADD COLUMN last_inference_at TIMESTAMP")


async def _ensure_inference_history_table(conn: aiosqlite.Connection):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS device_inference_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bucket_start TIMESTAMP,
            anomaly_score REAL NOT NULL,
            risk_score REAL NOT NULL,
            is_anomaly INTEGER DEFAULT 0,
            severity TEXT NOT NULL,
            features TEXT
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inference_history_device_time ON device_inference_history(device_id, timestamp)"
    )


async def _ensure_behavior_alerts_table(conn: aiosqlite.Connection):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS device_behavior_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bucket_start TIMESTAMP,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            score REAL NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            evidence TEXT,
            resolved INTEGER DEFAULT 0
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_alert_device_time ON device_behavior_alerts(device_id, timestamp)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_alert_device_type_bucket ON device_behavior_alerts(device_id, alert_type, bucket_start)"
    )


async def _ensure_device_model_config_table(conn: aiosqlite.Connection):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS device_model_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL UNIQUE,
            model_type TEXT NOT NULL DEFAULT 'isolation_forest',
            params TEXT DEFAULT '{}'
        )
    """)
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_device_model_config_device ON device_model_config(device_id)"
    )


async def _ensure_device_model_scores_table(conn: aiosqlite.Connection):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS device_model_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL,
            model_type TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bucket_start TIMESTAMP,
            anomaly_score REAL NOT NULL,
            risk_score REAL NOT NULL,
            is_anomaly INTEGER DEFAULT 0,
            UNIQUE(device_id, model_type, bucket_start)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_scores_device_type ON device_model_scores(device_id, model_type, timestamp)"
    )


async def _ensure_model_metadata_table(conn: aiosqlite.Connection):
    """Create model_metadata table for ML observability (Faza C).

    Stores per-device per-model training run stats so the gateway-api and
    dashboard can display model health information without re-reading joblib files.

    Uses ALTER TABLE ADD COLUMN (idempotent via OperationalError catch) to migrate
    pre-existing tables that were created without the ``device_id`` column.
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS model_metadata (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER,
            model_type TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trained_at TEXT,
            samples INTEGER,
            features INTEGER,
            contamination REAL,
            threshold REAL,
            score_mean REAL,
            score_std REAL,
            score_p5 REAL,
            score_p50 REAL,
            score_p95 REAL,
            estimated_anomaly_rate REAL,
            training_hours INTEGER,
            extra TEXT
        )
    """)

    # Migration: add columns that may be missing in tables created by older code.
    # SQLite does not support IF NOT EXISTS for ALTER TABLE ADD COLUMN, so we catch
    # the OperationalError that is raised when the column already exists.
    # IMPORTANT: run migrations BEFORE creating the index so that the index can
    # reference device_id even when the table was created without that column.
    migration_columns = [
        ("timestamp", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ("device_id", "INTEGER"),
        ("trained_at", "TEXT"),
        ("samples", "INTEGER"),
        ("features", "INTEGER"),
        ("contamination", "REAL"),
        ("threshold", "REAL"),
        ("score_mean", "REAL"),
        ("score_std", "REAL"),
        ("score_p5", "REAL"),
        ("score_p50", "REAL"),
        ("score_p95", "REAL"),
        ("estimated_anomaly_rate", "REAL"),
        ("training_hours", "INTEGER"),
        ("extra", "TEXT"),
    ]
    for col_name, col_type in migration_columns:
        try:
            await conn.execute(
                f"ALTER TABLE model_metadata ADD COLUMN {col_name} {col_type}"
            )
        except Exception:
            # Column already exists — safe to ignore (aiosqlite raises OperationalError)
            pass

    # Index is created after migrations so device_id is guaranteed to exist.
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_model_metadata_device_type ON model_metadata(device_id, model_type, trained_at)"
    )


# ──────────────────────────────────────────────────────────────────────────────
# Training configuration tables (Faza 3)
# ──────────────────────────────────────────────────────────────────────────────

# Default global training parameters — used when no DB row exists yet.
DEFAULT_TRAINING_CONFIG = {
    "training_hours": 168,
    "min_training_samples": 30,
    "contamination": 0.05,
    "n_estimators": 200,
    "feature_bucket_minutes": 5,
    "per_device_models": True,
}


async def _ensure_training_config_tables(conn: aiosqlite.Connection):
    """Create global_training_config and device_training_config tables.

    global_training_config is a single-row table holding cluster-wide defaults.
    device_training_config stores per-device overrides (sparse — only columns
    explicitly set by the user are non-NULL).
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS global_training_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            training_hours INTEGER NOT NULL DEFAULT 168,
            min_training_samples INTEGER NOT NULL DEFAULT 30,
            contamination REAL NOT NULL DEFAULT 0.05,
            n_estimators INTEGER NOT NULL DEFAULT 200,
            feature_bucket_minutes INTEGER NOT NULL DEFAULT 5,
            per_device_models INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Seed the single row if it doesn't exist.
    await conn.execute("""
        INSERT OR IGNORE INTO global_training_config (id) VALUES (1)
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS device_training_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL UNIQUE,
            training_hours INTEGER,
            min_training_samples INTEGER,
            contamination REAL,
            n_estimators INTEGER,
            feature_bucket_minutes INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_device_training_config_device ON device_training_config(device_id)"
    )


async def get_global_training_config() -> dict:
    """Return the global training config as a dict."""
    conn = await get_db_connection()
    try:
        cursor = await conn.execute(
            "SELECT training_hours, min_training_samples, contamination, "
            "n_estimators, feature_bucket_minutes, per_device_models, updated_at "
            "FROM global_training_config WHERE id = 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return dict(DEFAULT_TRAINING_CONFIG)
        return {
            "training_hours": row[0],
            "min_training_samples": row[1],
            "contamination": row[2],
            "n_estimators": row[3],
            "feature_bucket_minutes": row[4],
            "per_device_models": bool(row[5]),
            "updated_at": row[6],
        }
    finally:
        await conn.close()


async def set_global_training_config(updates: dict) -> dict:
    """Update global training config. Only keys present in ``updates`` are changed."""
    allowed = {"training_hours", "min_training_samples", "contamination",
               "n_estimators", "feature_bucket_minutes", "per_device_models"}
    filtered = {k: v for k, v in updates.items() if k in allowed and v is not None}
    if not filtered:
        return await get_global_training_config()
    # Convert per_device_models bool → int for SQLite
    if "per_device_models" in filtered:
        filtered["per_device_models"] = 1 if filtered["per_device_models"] else 0
    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    values = list(filtered.values())
    conn = await get_db_connection()
    try:
        await conn.execute(
            f"UPDATE global_training_config SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            values,
        )
        await conn.commit()
    finally:
        await conn.close()
    return await get_global_training_config()


async def get_device_training_config(device_id: int) -> dict | None:
    """Return per-device training config overrides, or None if no overrides set."""
    conn = await get_db_connection()
    try:
        cursor = await conn.execute(
            "SELECT training_hours, min_training_samples, contamination, "
            "n_estimators, feature_bucket_minutes, updated_at "
            "FROM device_training_config WHERE device_id = ?",
            (device_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "device_id": device_id,
            "training_hours": row[0],
            "min_training_samples": row[1],
            "contamination": row[2],
            "n_estimators": row[3],
            "feature_bucket_minutes": row[4],
            "updated_at": row[5],
        }
    finally:
        await conn.close()


async def set_device_training_config(device_id: int, updates: dict) -> dict:
    """Upsert per-device training config overrides."""
    allowed = {"training_hours", "min_training_samples", "contamination",
               "n_estimators", "feature_bucket_minutes"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return (await get_device_training_config(device_id)) or {"device_id": device_id}
    cols = ["device_id"] + list(filtered.keys())
    placeholders = ", ".join("?" for _ in cols)
    values = [device_id] + list(filtered.values())
    on_conflict = ", ".join(f"{k} = excluded.{k}" for k in filtered)
    conn = await get_db_connection()
    try:
        await conn.execute(
            f"INSERT INTO device_training_config ({', '.join(cols)}, updated_at) "
            f"VALUES ({placeholders}, CURRENT_TIMESTAMP) "
            f"ON CONFLICT(device_id) DO UPDATE SET {on_conflict}, updated_at = CURRENT_TIMESTAMP",
            values,
        )
        await conn.commit()
    finally:
        await conn.close()
    return (await get_device_training_config(device_id)) or {"device_id": device_id}


async def get_effective_training_config(device_id: int) -> dict:
    """Return merged training config: global defaults overridden by per-device values.

    Per-device values that are NULL (not set) fall through to the global default.
    """
    global_cfg = await get_global_training_config()
    device_cfg = await get_device_training_config(device_id)
    merged = dict(global_cfg)
    merged["device_id"] = device_id
    if device_cfg:
        for key in ("training_hours", "min_training_samples", "contamination",
                     "n_estimators", "feature_bucket_minutes"):
            if device_cfg.get(key) is not None:
                merged[key] = device_cfg[key]
        merged["has_overrides"] = True
    else:
        merged["has_overrides"] = False
    return merged


# ──────────────────────────────────────────────────────────────────────────────
# ML observability — training metadata persistence (Faza C)
# ──────────────────────────────────────────────────────────────────────────────

async def save_model_training_metadata(
    device_id: int | None,
    model_type: str,
    samples: int,
    features_count: int,
    contamination: float,
    detector: BaseDetector,
    training_hours: int = 0,
    extra: dict | None = None,
) -> None:
    """Persist training run statistics to the model_metadata table.

    Called by train.py after each successful detector.fit() call.
    Stores the adaptive threshold and score distribution so that
    the gateway-api ml-status endpoint can report model health
    without loading joblib files.

    Parameters
    ----------
    device_id : int | None
        Device the model was trained for (None for global models).
    model_type : str
        One of AVAILABLE_MODEL_TYPES.
    samples : int
        Number of 5-minute feature buckets used for training.
    features_count : int
        Number of features (len(FEATURE_COLUMNS)).
    contamination : float
        Effective contamination/nu rate used during training.
    detector : BaseDetector
        Fitted detector with _score_stats and threshold populated.
    training_hours : int
        Lookback window in hours used to fetch training flows.
    extra : dict | None
        Additional JSON-serialisable context (e.g. reconstruction error stats).
    """
    score_stats = getattr(detector, '_score_stats', {}) or {}
    # Estimate anomaly rate: fraction of training scores below adaptive threshold
    # We approximate from score distribution percentiles
    estimated_anomaly_rate = contamination  # conservative estimate

    conn = await get_db_connection()
    try:
        # _ensure_model_metadata_table is called at startup via ensure_schema(); we
        # do NOT call it here to avoid redundant DDL on every training run.
        await conn.execute(
            """
            INSERT INTO model_metadata (
                device_id, model_type, trained_at, samples, features,
                contamination, threshold,
                score_mean, score_std, score_p5, score_p50, score_p95,
                estimated_anomaly_rate, training_hours, extra, version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                model_type,
                datetime.now(timezone.utc).isoformat(),
                samples,
                features_count,
                round(contamination, 6),
                round(detector.threshold, 6),
                round(score_stats.get("mean", 0.0), 6),
                round(score_stats.get("std", 0.0), 6),
                round(score_stats.get("p5",  0.0), 6),
                round(score_stats.get("p50", 0.0), 6),
                round(score_stats.get("p95", 0.0), 6),
                round(estimated_anomaly_rate, 6),
                training_hours,
                json.dumps(extra or {}),
                "1.0",  # version — required by legacy schema NOT NULL constraint
            ),
        )
        # Keep only the last 10 training runs per device+model_type
        await conn.execute(
            """
            DELETE FROM model_metadata
            WHERE device_id IS ? AND model_type = ?
              AND id NOT IN (
                  SELECT id FROM model_metadata
                  WHERE device_id IS ? AND model_type = ?
                  ORDER BY id DESC LIMIT 10
              )
            """,
            (device_id, model_type, device_id, model_type),
        )
        await conn.commit()
    finally:
        await conn.close()


async def get_model_metadata(
    device_id: int | None = None,
    model_type: str | None = None,
    limit: int = 1,
) -> list[dict]:
    """Return the most recent model_metadata rows for a device/model combination.

    Used by gateway-api /metrics/ml-status to surface per-model training stats.
    """
    conn = await get_db_connection()
    try:
        conditions = []
        params: list = []
        if device_id is not None:
            conditions.append("device_id = ?")
            params.append(device_id)
        if model_type is not None:
            conditions.append("model_type = ?")
            params.append(model_type)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        cursor = await conn.execute(
            f"""
            SELECT device_id, model_type, trained_at, samples, features,
                   contamination, threshold,
                   score_mean, score_std, score_p5, score_p50, score_p95,
                   estimated_anomaly_rate, training_hours
            FROM model_metadata {where}
            ORDER BY id DESC LIMIT ?
            """,
            params,
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    finally:
        await conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# Legacy save/load helpers (kept for API compatibility)
# ──────────────────────────────────────────────────────────────────────────────

async def save_anomaly(
    device_id: int,
    anomaly_type: str,
    severity: str,
    score: float,
    description: str,
    features: dict,
    conn: aiosqlite.Connection | None = None,
):
    """Insert an anomaly row. If ``conn`` is provided it is reused (caller commits)."""
    _close = conn is None
    if conn is None:
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute("""
        INSERT INTO anomalies (device_id, anomaly_type, severity, score, description, features)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (device_id, anomaly_type, severity, score, description, json.dumps(features)))
    if _close:
        await conn.commit()
        await conn.close()
    log.warning("anomaly_saved", device_id=device_id, type=anomaly_type, score=score)


async def update_device_risk_score(
    device_id: int,
    risk_score: float,
    last_inference_score: float | None = None,
    conn: aiosqlite.Connection | None = None,
):
    """Update device risk score. If ``conn`` is provided it is reused (caller commits)."""
    _close = conn is None
    if conn is None:
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
    await _ensure_device_inference_columns(conn)
    await conn.execute("""
        UPDATE devices
        SET risk_score = ?,
            last_inference_score = ?,
            last_inference_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (risk_score, last_inference_score, device_id))
    if _close:
        await conn.commit()
        await conn.close()


async def save_inference_result(
    device_id: int,
    bucket_start,
    anomaly_score: float,
    risk_score: float,
    is_anomaly: bool,
    severity: str,
    features: dict,
    retention_days: int = 7,
    conn: aiosqlite.Connection | None = None,
):
    """Insert inference history. If ``conn`` is provided it is reused (caller commits)."""
    _close = conn is None
    if conn is None:
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
    await _ensure_inference_history_table(conn)
    await conn.execute(
        """
        INSERT INTO device_inference_history (
            device_id, bucket_start, anomaly_score, risk_score, is_anomaly, severity, features
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            device_id,
            bucket_start.isoformat(sep=" ") if bucket_start is not None else None,
            anomaly_score,
            risk_score,
            1 if is_anomaly else 0,
            severity,
            json.dumps(features),
        ),
    )
    await conn.execute(
        "DELETE FROM device_inference_history WHERE timestamp < datetime('now', '-' || ? || ' days')",
        (retention_days,),
    )
    if _close:
        await conn.commit()
        await conn.close()


async def save_behavior_alert(
    device_id: int,
    bucket_start,
    alert_type: str,
    severity: str,
    score: float,
    title: str,
    description: str,
    evidence: dict,
    retention_days: int = 7,
    conn: aiosqlite.Connection | None = None,
):
    """Insert a behavior alert (dedup by device+type+bucket). If ``conn`` provided, reused."""
    _close = conn is None
    if conn is None:
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA busy_timeout=5000")
    await _ensure_behavior_alerts_table(conn)
    bucket_value = bucket_start.isoformat(sep=" ") if bucket_start is not None else None
    cursor = await conn.execute(
        """
        SELECT id FROM device_behavior_alerts
        WHERE device_id = ? AND alert_type = ?
          AND ((bucket_start IS NULL AND ? IS NULL) OR bucket_start = ?)
        LIMIT 1
        """,
        (device_id, alert_type, bucket_value, bucket_value),
    )
    existing = await cursor.fetchone()
    if existing:
        if _close:
            await conn.close()
        return
    await conn.execute(
        """
        INSERT INTO device_behavior_alerts (
            device_id, bucket_start, alert_type, severity, score, title, description, evidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            device_id,
            bucket_value,
            alert_type,
            severity,
            score,
            title,
            description,
            json.dumps(evidence),
        ),
    )
    if _close:
        await conn.commit()
        await conn.close()


async def get_device_model_configs() -> dict[int, str]:
    """Return {device_id: model_type} for all configured devices."""
    conn = await get_db_connection()
    try:
        cursor = await conn.execute("SELECT device_id, model_type FROM device_model_config")
        rows = await cursor.fetchall()
        return {int(row[0]): str(row[1]) for row in rows}
    finally:
        await conn.close()


async def batch_save_inference_cycle(results: list[dict], retention_days: int = 7,
                                     alerts_retention_days: int = 14):
    """Write all inference results for one cycle using a single DB connection.

    Each item in ``results`` must contain:
        device_id, bucket_start, anomaly_score, risk_score, is_anomaly,
        severity, features (dict), behavior_alerts (list[dict]),
        is_isolation_forest_anomaly (bool), model_type (str)

    Parameters
    ----------
    retention_days : int
        Days to keep device_inference_history rows (default 7).
    alerts_retention_days : int
        Days to keep device_behavior_alerts rows (default 14, per AGENTS.md).
    """
    if not results:
        return

    conn = await get_db_connection()
    try:
        for r in results:
            device_id = r["device_id"]
            bucket_start = r["bucket_start"]
            risk_score = r["risk_score"]
            anomaly_score = r["anomaly_score"]
            is_anomaly = r["is_anomaly"]
            severity = r["severity"]
            features = r["features"]
            behavior_alerts = r.get("behavior_alerts", [])
            bucket_value = bucket_start.isoformat(sep=" ") if bucket_start is not None else None

            # 1. Update device risk score
            await conn.execute(
                """
                UPDATE devices
                SET risk_score = ?,
                    last_inference_score = ?,
                    last_inference_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (risk_score, float(anomaly_score), device_id),
            )

            # 2. Insert inference history
            await conn.execute(
                """
                INSERT INTO device_inference_history (
                    device_id, bucket_start, anomaly_score, risk_score, is_anomaly, severity, features
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    device_id,
                    bucket_value,
                    float(anomaly_score),
                    float(risk_score),
                    1 if is_anomaly else 0,
                    severity,
                    json.dumps(features),
                ),
            )

            # 3. Insert behavior alerts (skip duplicates)
            for alert in behavior_alerts:
                alert_type = alert["alert_type"]
                alert_bucket = bucket_value
                cursor = await conn.execute(
                    """
                    SELECT id FROM device_behavior_alerts
                    WHERE device_id = ? AND alert_type = ?
                      AND ((bucket_start IS NULL AND ? IS NULL) OR bucket_start = ?)
                    LIMIT 1
                    """,
                    (device_id, alert_type, alert_bucket, alert_bucket),
                )
                existing = await cursor.fetchone()
                if existing:
                    continue
                await conn.execute(
                    """
                    INSERT INTO device_behavior_alerts (
                        device_id, bucket_start, alert_type, severity, score, title, description, evidence
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        alert_bucket,
                        alert_type,
                        alert["severity"],
                        float(alert["score"]),
                        alert["title"],
                        alert["description"],
                        json.dumps(alert["evidence"]),
                    ),
                )

            # 4. Insert anomaly if flagged by the active model (with dedup)
            if r.get("is_isolation_forest_anomaly"):
                model_type = r.get("model_type", "isolation_forest")
                # Deduplicate: skip if an anomaly already exists for this
                # device + model_type within the same feature bucket.
                bucket_iso = bucket_start.isoformat(sep=" ") if bucket_start is not None else None
                existing = None
                if bucket_iso:
                    cursor = await conn.execute(
                        """SELECT id FROM anomalies
                           WHERE device_id = ? AND anomaly_type = ?
                             AND timestamp >= ? AND timestamp < datetime(?, '+5 minutes')
                           LIMIT 1""",
                        (device_id, model_type, bucket_iso, bucket_iso),
                    )
                    existing = await cursor.fetchone()
                if existing is None:
                    await conn.execute(
                        """
                        INSERT INTO anomalies (device_id, anomaly_type, severity, score, description, features)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            device_id,
                            model_type,
                            severity,
                            float(anomaly_score),
                            f"{model_type} anomaly score={anomaly_score:.4f}",
                            json.dumps(r.get("raw_features") or {}),
                        ),
                    )

        # Retention is handled exclusively by run_retention_cleanup() (batched DELETEs
        # with LIMIT to avoid long write-lock bursts). Inline unbatched DELETEs removed.
        await conn.commit()
    finally:
        await conn.close()


async def batch_save_model_scores(scores: list[dict]):
    """Save per-model scores to device_model_scores (upsert by device+model+bucket)."""
    if not scores:
        return
    conn = await get_db_connection()
    try:
        for s in scores:
            bucket_value = s["bucket_start"].isoformat(sep=" ") if s.get("bucket_start") is not None else None
            await conn.execute(
                """
                INSERT INTO device_model_scores (device_id, model_type, bucket_start, anomaly_score, risk_score, is_anomaly)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id, model_type, bucket_start)
                DO UPDATE SET anomaly_score=excluded.anomaly_score, risk_score=excluded.risk_score,
                              is_anomaly=excluded.is_anomaly, timestamp=CURRENT_TIMESTAMP
                """,
                (
                    s["device_id"],
                    s["model_type"],
                    bucket_value,
                    float(s["anomaly_score"]),
                    float(s["risk_score"]),
                    1 if s["is_anomaly"] else 0,
                ),
            )
        # Retention: keep 7 days
        await conn.execute(
            "DELETE FROM device_model_scores WHERE timestamp < datetime('now', '-7 days')"
        )
        await conn.commit()
    finally:
        await conn.close()


async def batch_get_latest_flow_timestamps(device_ids: list[int]) -> dict[int, str | None]:
    """Return MAX(timestamp) from traffic_flows for each device in one query."""
    if not device_ids:
        return {}
    conn = await get_db_connection()
    try:
        placeholders = ",".join("?" * len(device_ids))
        cursor = await conn.execute(
            f"SELECT device_id, MAX(timestamp) FROM traffic_flows "
            f"WHERE device_id IN ({placeholders}) GROUP BY device_id",
            device_ids,
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()
    result = {did: None for did in device_ids}
    for row in rows:
        result[row[0]] = row[1]
    return result


async def batch_get_latest_trained_at(device_ids: list[int]) -> dict[int, str | None]:
    """Return MAX(trained_at) from model_metadata for each device in one query."""
    if not device_ids:
        return {}
    conn = await get_db_connection()
    try:
        placeholders = ",".join("?" * len(device_ids))
        cursor = await conn.execute(
            f"SELECT device_id, MAX(trained_at) FROM model_metadata "
            f"WHERE device_id IN ({placeholders}) GROUP BY device_id",
            device_ids,
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()
    result = {did: None for did in device_ids}
    for row in rows:
        result[row[0]] = row[1]
    return result


async def batch_get_effective_training_configs(device_ids: list[int]) -> dict[int, dict]:
    """Return merged training configs for all devices in two queries.

    Fetches global config once, then all per-device overrides in one query.
    """
    if not device_ids:
        return {}
    global_cfg = await get_global_training_config()
    conn = await get_db_connection()
    try:
        placeholders = ",".join("?" * len(device_ids))
        cursor = await conn.execute(
            f"SELECT device_id, training_hours, min_training_samples, contamination, "
            f"n_estimators, feature_bucket_minutes "
            f"FROM device_training_config WHERE device_id IN ({placeholders})",
            device_ids,
        )
        rows = await cursor.fetchall()
    finally:
        await conn.close()
    overrides = {}
    for row in rows:
        overrides[row[0]] = {
            "training_hours": row[1],
            "min_training_samples": row[2],
            "contamination": row[3],
            "n_estimators": row[4],
            "feature_bucket_minutes": row[5],
        }
    configs = {}
    for did in device_ids:
        merged = dict(global_cfg)
        merged["device_id"] = did
        dev_cfg = overrides.get(did)
        if dev_cfg:
            for key in ("training_hours", "min_training_samples", "contamination",
                        "n_estimators", "feature_bucket_minutes"):
                if dev_cfg.get(key) is not None:
                    merged[key] = dev_cfg[key]
            merged["has_overrides"] = True
        else:
            merged["has_overrides"] = False
        configs[did] = merged
    return configs


async def get_latest_flow_timestamp(device_id: int | None = None) -> str | None:
    """Return the MAX(timestamp) from traffic_flows for a device (or globally).

    Used by train.py to skip training when no new flows have arrived since
    the last training run.
    """
    conn = await get_db_connection()
    try:
        if device_id is not None:
            cursor = await conn.execute(
                "SELECT MAX(timestamp) FROM traffic_flows WHERE device_id = ?",
                (device_id,),
            )
        else:
            cursor = await conn.execute("SELECT MAX(timestamp) FROM traffic_flows")
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None
    finally:
        await conn.close()


async def get_latest_trained_at(device_id: int | None = None) -> str | None:
    """Return the most recent trained_at timestamp from model_metadata.

    Used by train.py to compare against latest flow timestamp and skip
    training when there's no new data.
    """
    conn = await get_db_connection()
    try:
        if device_id is not None:
            cursor = await conn.execute(
                "SELECT MAX(trained_at) FROM model_metadata WHERE device_id = ?",
                (device_id,),
            )
        else:
            cursor = await conn.execute("SELECT MAX(trained_at) FROM model_metadata")
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None
    finally:
        await conn.close()
