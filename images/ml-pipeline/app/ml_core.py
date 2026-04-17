import os
import structlog
import aiosqlite
import pandas as pd
import numpy as np
import json
from datetime import datetime, timedelta
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


class FeatureExtractor:
    FEATURE_COLUMNS = [
        'total_bytes', 'packets', 'unique_destinations',
        'unique_ports', 'dns_queries', 'avg_bytes_per_packet',
        'packet_rate', 'connection_duration_avg'
    ]
    
    def __init__(self, bucket_minutes: int | None = None):
        self.bucket_minutes = bucket_minutes or int(os.getenv("FEATURE_BUCKET_MINUTES", "5"))

    def extract_features(self, flows: pd.DataFrame) -> pd.DataFrame:
        if flows.empty:
            return pd.DataFrame(columns=['device_id', 'bucket_start', *self.FEATURE_COLUMNS])
        
        features = []
        bucket = f"{self.bucket_minutes}min"
        # Avoid full copy: assign() returns a new df with added column
        flows_with_bucket = flows.assign(bucket_start=flows['timestamp'].dt.floor(bucket))
        
        for (device_id, bucket_start), group in flows_with_bucket.groupby(['device_id', 'bucket_start']):
            device_flows = group.sort_values('timestamp')
            
            total_bytes = device_flows['bytes_sent'].sum() + device_flows['bytes_received'].sum()
            packets = len(device_flows)
            unique_destinations = device_flows['dst_ip'].nunique()
            unique_ports = device_flows['dst_port'].nunique()
            dns_queries = device_flows['dns_query'].notna().sum()
            
            total_packet_bytes = device_flows['bytes_sent'].sum() + device_flows['bytes_received'].sum()
            avg_bytes_per_packet = total_packet_bytes / packets if packets > 0 else 0
            
            time_span = (device_flows['timestamp'].max() - device_flows['timestamp'].min()).total_seconds()
            packet_rate = packets / time_span if time_span > 0 else 0
            
            if len(device_flows) > 1:
                durations = device_flows['timestamp'].diff().dropna().dt.total_seconds()
                connection_duration_avg = durations.mean() if len(durations) > 0 else 0
            else:
                connection_duration_avg = 0
            
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
            })
        
        return pd.DataFrame(features)


class AnomalyDetector:
    # Module-level model cache: device_id -> (mtime, model)
    _model_cache: dict = {}

    def __init__(self, model_path: str):
        self.model_path = model_path
        self.model = None
        self.threshold = float(os.getenv("ANOMALY_THRESHOLD", "-0.5"))
    
    def _model_file(self, device_id: int | None = None) -> str:
        import os
        name = f"isolation_forest_model_device_{device_id}.joblib" if device_id is not None else "isolation_forest_model.joblib"
        return os.path.join(self.model_path, name)

    def load_model(self, device_id: int | None = None):
        import joblib
        
        model_file = self._model_file(device_id)
        if not os.path.exists(model_file):
            return False

        mtime = os.path.getmtime(model_file)
        cached = AnomalyDetector._model_cache.get(device_id)
        if cached is not None and cached[0] == mtime:
            self.model = cached[1]
            return True

        self.model = joblib.load(model_file)
        AnomalyDetector._model_cache[device_id] = (mtime, self.model)
        log.info("model_loaded", path=model_file, device_id=device_id)
        return True
    
    def save_model(self, model, device_id: int | None = None):
        import joblib
        import os
        
        os.makedirs(self.model_path, exist_ok=True)
        model_file = self._model_file(device_id)
        joblib.dump(model, model_file)
        log.info("model_saved", path=model_file, device_id=device_id)
        self.model = model

    def model_exists(self, device_id: int | None = None) -> bool:
        return os.path.exists(self._model_file(device_id))
    
    def detect(self, features: pd.DataFrame) -> List[Dict]:
        if self.model is None or features.empty:
            return []

        # Use the feature set defined by the extractor.
        X = features[FeatureExtractor.FEATURE_COLUMNS].values
        
        scores = self.model.decision_function(X)
        predictions = self.model.predict(X)
        
        anomalies = []
        for idx, (score, pred) in enumerate(zip(scores, predictions)):
            if score < self.threshold:
                device_id = features.iloc[idx]['device_id']
                anomalies.append({
                    'device_id': int(device_id),
                    'anomaly_score': float(score),
                    'severity': 'critical' if score < self.threshold * 2 else 'warning',
                    'features': features.iloc[idx][FeatureExtractor.FEATURE_COLUMNS].to_dict()
                })
        
        return anomalies

    def score(self, features: pd.DataFrame) -> List[Dict]:
        if self.model is None or features.empty:
            return []

        X = features[FeatureExtractor.FEATURE_COLUMNS].values
        scores = self.model.decision_function(X)

        rows = []
        for idx, score in enumerate(scores):
            rows.append(
                {
                    'device_id': int(features.iloc[idx]['device_id']),
                    'bucket_start': features.iloc[idx].get('bucket_start'),
                    'anomaly_score': float(score),
                    'is_anomaly': bool(score < self.threshold),
                    'severity': 'critical' if score < self.threshold * 2 else 'warning',
                    'features': features.iloc[idx][FeatureExtractor.FEATURE_COLUMNS].to_dict(),
                }
            )
        return rows


async def get_device_flows(device_id: int, hours: int = 24) -> pd.DataFrame:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    
    # Enable WAL mode for better concurrent read/write performance
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
    await conn.close()
    
    if not rows:
        return pd.DataFrame()
    
    df = pd.DataFrame([dict(row) for row in rows])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    if 'flags' in df.columns:
        df['flags'] = df['flags'].apply(lambda value: json.loads(value) if isinstance(value, str) and value else (value or {}))
        df['dns_rcode'] = df['flags'].apply(lambda value: value.get('dns_rcode') if isinstance(value, dict) else None)
        df['icmp_type'] = df['flags'].apply(lambda value: value.get('icmp_type') if isinstance(value, dict) else None)
        df['icmp_code'] = df['flags'].apply(lambda value: value.get('icmp_code') if isinstance(value, dict) else None)
    return df


async def get_all_recent_flows(hours: int = 24) -> pd.DataFrame:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    
    # Enable WAL mode for better concurrent read/write performance
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    
    cursor = await conn.execute("""
        SELECT device_id, timestamp, src_ip, dst_ip, src_port, dst_port, 
               protocol, bytes_sent, bytes_received, dns_query, flags
        FROM traffic_flows
        WHERE timestamp >= datetime('now', '-' || ? || ' hours')
        ORDER BY device_id, timestamp
    """, (hours,))
    
    rows = await cursor.fetchall()
    await conn.close()
    
    if not rows:
        return pd.DataFrame()
    
    df = pd.DataFrame([dict(row) for row in rows])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    if 'flags' in df.columns:
        df['flags'] = df['flags'].apply(lambda value: json.loads(value) if isinstance(value, str) and value else (value or {}))
        df['dns_rcode'] = df['flags'].apply(lambda value: value.get('dns_rcode') if isinstance(value, dict) else None)
        df['icmp_type'] = df['flags'].apply(lambda value: value.get('icmp_type') if isinstance(value, dict) else None)
        df['icmp_code'] = df['flags'].apply(lambda value: value.get('icmp_code') if isinstance(value, dict) else None)
    return df


async def get_db_connection() -> aiosqlite.Connection:
    """Open a single SQLite connection with WAL mode. Caller is responsible for closing."""
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    return conn


async def ensure_schema():
    """Run all schema migrations once at startup, not per-cycle."""
    conn = await get_db_connection()
    try:
        await _ensure_device_inference_columns(conn)
        await _ensure_inference_history_table(conn)
        await _ensure_behavior_alerts_table(conn)
        await conn.commit()
        log.info("schema_ensured")
    finally:
        await conn.close()


async def save_anomaly(device_id: int, anomaly_type: str, severity: str, 
                       score: float, description: str, features: dict):
    conn = await aiosqlite.connect(DB_PATH)
    
    # Enable WAL mode for better concurrent read/write performance
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA synchronous=NORMAL")
    await conn.execute("PRAGMA busy_timeout=5000")
    
    await conn.execute("""
        INSERT INTO anomalies (device_id, anomaly_type, severity, score, description, features)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (device_id, anomaly_type, severity, score, description, json.dumps(features)))
    
    await conn.commit()
    await conn.close()
    
    log.warning("anomaly_saved", device_id=device_id, type=anomaly_type, score=score)


async def _ensure_device_inference_columns(conn: aiosqlite.Connection):
    cursor = await conn.execute("PRAGMA table_info(devices)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "last_inference_score" not in cols:
        await conn.execute(
            "ALTER TABLE devices ADD COLUMN last_inference_score REAL"
        )
    if "last_inference_at" not in cols:
        await conn.execute(
            "ALTER TABLE devices ADD COLUMN last_inference_at TIMESTAMP"
        )


async def _ensure_inference_history_table(conn: aiosqlite.Connection):
    await conn.execute(
        """
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
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_inference_history_device_time ON device_inference_history(device_id, timestamp)"
    )


async def _ensure_behavior_alerts_table(conn: aiosqlite.Connection):
    await conn.execute(
        """
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
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_alert_device_time ON device_behavior_alerts(device_id, timestamp)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_behavior_alert_device_type_bucket ON device_behavior_alerts(device_id, alert_type, bucket_start)"
    )


async def update_device_risk_score(device_id: int, risk_score: float, last_inference_score: float | None = None):
    conn = await aiosqlite.connect(DB_PATH)
    
    # Enable WAL mode for better concurrent read/write performance
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
):
    conn = await aiosqlite.connect(DB_PATH)
    
    # Enable WAL mode for better concurrent read/write performance
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
):
    conn = await aiosqlite.connect(DB_PATH)
    
    # Enable WAL mode for better concurrent read/write performance
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
    await conn.commit()
    await conn.close()


async def batch_save_inference_cycle(results: list[dict], retention_days: int = 7):
    """
    Write all inference results, behavior alerts and anomalies for one cycle using a
    single DB connection, eliminating the N+1 connect/close pattern.

    Each item in `results` is a dict with keys:
        device_id, bucket_start, anomaly_score, risk_score, is_anomaly, severity,
        features (dict), behavior_alerts (list[dict]), is_isolation_forest_anomaly (bool)
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

            # 4. Insert anomaly if flagged
            if r.get("is_isolation_forest_anomaly"):
                await conn.execute(
                    """
                    INSERT INTO anomalies (device_id, anomaly_type, severity, score, description, features)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        "isolation_forest",
                        severity,
                        float(anomaly_score),
                        f"IsolationForest anomaly score={anomaly_score:.4f}",
                        json.dumps(r.get("raw_features") or {}),
                    ),
                )

        # Prune old data in one pass
        await conn.execute(
            "DELETE FROM device_inference_history WHERE timestamp < datetime('now', '-' || ? || ' days')",
            (retention_days,),
        )
        await conn.execute(
            "DELETE FROM device_behavior_alerts WHERE timestamp < datetime('now', '-' || ? || ' days')",
            (retention_days,),
        )

        await conn.commit()
    finally:
        await conn.close()
