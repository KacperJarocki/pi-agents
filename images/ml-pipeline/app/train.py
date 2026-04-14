import os
import asyncio
import structlog
import aiosqlite
from datetime import datetime
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler
import joblib
import numpy as np

from .ml_core import (
    DB_PATH, MODEL_DIR, FeatureExtractor, AnomalyDetector,
    get_all_recent_flows, save_anomaly, update_device_risk_score
)

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()


def train_model():
    log.info("training_started")
    
    try:
        flows_df = asyncio.run(_load_training_data())
        
        if len(flows_df) < 100:
            log.warning("insufficient_training_data", samples=len(flows_df))
            return False
        
        extractor = FeatureExtractor()
        features = extractor.extract_features(flows_df)
        
        if features.empty:
            log.warning("no_features_extracted")
            return False
        
        X = features[FeatureExtractor.FEATURE_COLUMNS].values
        
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)
        
        contamination = min(0.1, max(0.01, 1.0 / len(features)))
        
        model = IsolationForest(
            n_estimators=100,
            contamination=contamination,
            max_samples='auto',
            random_state=42,
            n_jobs=-1
        )
        
        model.fit(X_scaled)
        
        model_path = os.path.join(MODEL_DIR, "isolation_forest_model.joblib")
        scaler_path = os.path.join(MODEL_DIR, "scaler.joblib")
        
        os.makedirs(MODEL_DIR, exist_ok=True)
        joblib.dump(model, model_path)
        joblib.dump(scaler, scaler_path)
        
        asyncio.run(_save_model_metadata(len(features), contamination))
        
        log.info("training_completed", 
                samples=len(features),
                contamination=contamination,
                model_path=model_path)
        
        return True
        
    except Exception as e:
        log.error("training_failed", error=str(e))
        return False


async def _load_training_data():
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    
    cursor = await conn.execute("""
        SELECT device_id, timestamp, src_ip, dst_ip, src_port, dst_port, 
               protocol, bytes_sent, bytes_received, dns_query
        FROM traffic_flows
        WHERE timestamp >= datetime('now', '-7 days')
        ORDER BY device_id, timestamp
    """)
    
    rows = await cursor.fetchall()
    await conn.close()
    
    if not rows:
        return None
    
    import pandas as pd
    df = pd.DataFrame([dict(row) for row in rows])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    return df


async def _save_model_metadata(samples: int, contamination: float):
    conn = await aiosqlite.connect(DB_PATH)
    
    await conn.execute("""
        UPDATE model_metadata SET is_active = 0
    """)
    
    await conn.execute("""
        INSERT INTO model_metadata (model_type, version, training_samples, features_used, parameters, is_active)
        VALUES (?, ?, ?, ?, ?, 1)
    """, (
        "IsolationForest",
        datetime.now().strftime("%Y%m%d_%H%M%S"),
        samples,
        str(FeatureExtractor.FEATURE_COLUMNS),
        str({"contamination": contamination, "n_estimators": 100}),
    ))
    
    await conn.commit()
    await conn.close()


if __name__ == "__main__":
    train_model()
