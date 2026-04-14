import os
import asyncio
import time
import structlog
import joblib
from datetime import datetime

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

INFERENCE_INTERVAL = int(os.getenv("INFERENCE_INTERVAL", "300"))


def run_inference_loop():
    detector = AnomalyDetector(MODEL_DIR)
    
    if not detector.load_model():
        log.warning("no_model_found_will_wait")
    
    while True:
        try:
            asyncio.run(run_inference(detector))
            time.sleep(INFERENCE_INTERVAL)
        except KeyboardInterrupt:
            log.info("inference_stopped")
            break
        except Exception as e:
            log.error("inference_error", error=str(e))
            time.sleep(60)


async def run_inference(detector: AnomalyDetector):
    if detector.model is None:
        if not detector.load_model():
            log.warning("skipping_inference_no_model")
            return
    
    log.info("inference_started")
    
    flows_df = await get_all_recent_flows(hours=24)
    
    if flows_df.empty:
        log.info("no_flows_to_analyze")
        return
    
    extractor = FeatureExtractor()
    features = extractor.extract_features(flows_df)
    
    if features.empty:
        log.info("no_features_extracted")
        return
    
    anomalies = detector.detect(features)
    
    for anomaly in anomalies:
        description = _generate_description(anomaly)
        
        await save_anomaly(
            device_id=anomaly['device_id'],
            anomaly_type="behavioral_anomaly",
            severity=anomaly['severity'],
            score=anomaly['anomaly_score'],
            description=description,
            features=anomaly['features']
        )
        
        risk_score = abs(anomaly['anomaly_score']) * 100
        await update_device_risk_score(anomaly['device_id'], min(risk_score, 100))
    
    log.info("inference_completed", 
            devices_analyzed=len(features),
            anomalies_found=len(anomalies))


def _generate_description(anomaly: dict) -> str:
    features = anomaly['features']
    
    parts = []
    
    if features.get('total_bytes', 0) > 10000000:
        parts.append("high data volume")
    
    if features.get('unique_destinations', 0) > 20:
        parts.append("unusual number of destinations")
    
    if features.get('unique_ports', 0) > 10:
        parts.append("multiple ports activity")
    
    if features.get('dns_queries', 0) > 100:
        parts.append("excessive DNS queries")
    
    if features.get('packet_rate', 0) > 100:
        parts.append("high packet rate")
    
    if not parts:
        parts.append("anomalous behavior pattern detected")
    
    return "; ".join(parts)


if __name__ == "__main__":
    run_inference_loop()
