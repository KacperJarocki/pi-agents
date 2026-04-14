import os
import structlog
import aiosqlite
import pandas as pd
import numpy as np
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
    
    def extract_features(self, flows: pd.DataFrame) -> pd.DataFrame:
        if flows.empty:
            return pd.DataFrame(columns=self.FEATURE_COLUMNS)
        
        features = []
        
        for device_id, group in flows.groupby('device_id'):
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
    def __init__(self, model_path: str):
        self.model_path = model_path
        self.model = None
        self.threshold = float(os.getenv("ANOMALY_THRESHOLD", "-0.5"))
    
    def load_model(self):
        import joblib
        import os
        
        model_file = os.path.join(self.model_path, "isolation_forest_model.joblib")
        if os.path.exists(model_file):
            self.model = joblib.load(model_file)
            log.info("model_loaded", path=model_file)
            return True
        return False
    
    def save_model(self, model):
        import joblib
        import os
        
        os.makedirs(self.model_path, exist_ok=True)
        model_file = os.path.join(self.model_path, "isolation_forest_model.joblib")
        joblib.dump(model, model_file)
        log.info("model_saved", path=model_file)
        self.model = model
    
    def detect(self, features: pd.DataFrame) -> List[Dict]:
        if self.model is None or features.empty:
            return []
        
        X = features[self.FEATURE_COLUMNS].values
        
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
                    'features': features.iloc[idx][self.FEATURE_COLUMNS].to_dict()
                })
        
        return anomalies


async def get_device_flows(device_id: int, hours: int = 24) -> pd.DataFrame:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    
    cursor = await conn.execute("""
        SELECT device_id, timestamp, src_ip, dst_ip, src_port, dst_port, 
               protocol, bytes_sent, bytes_received, dns_query
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
    return df


async def get_all_recent_flows(hours: int = 24) -> pd.DataFrame:
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    
    cursor = await conn.execute("""
        SELECT device_id, timestamp, src_ip, dst_ip, src_port, dst_port, 
               protocol, bytes_sent, bytes_received, dns_query
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
    return df


async def save_anomaly(device_id: int, anomaly_type: str, severity: str, 
                       score: float, description: str, features: dict):
    conn = await aiosqlite.connect(DB_PATH)
    
    await conn.execute("""
        INSERT INTO anomalies (device_id, anomaly_type, severity, score, description, features)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (device_id, anomaly_type, severity, score, description, str(features)))
    
    await conn.commit()
    await conn.close()
    
    log.warning("anomaly_saved", device_id=device_id, type=anomaly_type, score=score)


async def update_device_risk_score(device_id: int, risk_score: float):
    conn = await aiosqlite.connect(DB_PATH)
    
    await conn.execute("""
        UPDATE devices SET risk_score = ? WHERE id = ?
    """, (risk_score, device_id))
    
    await conn.commit()
    await conn.close()
