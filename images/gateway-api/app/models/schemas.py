from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, JSON, Index
from sqlalchemy.sql import func
from ..core.database import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    mac_address = Column(String(17), unique=True, nullable=False, index=True)
    ip_address = Column(String(15), nullable=False)
    hostname = Column(String(255), nullable=True)
    device_type = Column(String(50), nullable=True)
    first_seen = Column(DateTime, server_default=func.now())
    last_seen = Column(DateTime, server_default=func.now(), onupdate=func.now())
    is_active = Column(Boolean, default=True)
    risk_score = Column(Float, default=0.0)
    metadata = Column(JSON, nullable=True)


class TrafficFlow(Base):
    __tablename__ = "traffic_flows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Integer, nullable=False, index=True)
    timestamp = Column(DateTime, server_default=func.now(), index=True)
    src_ip = Column(String(15), nullable=False)
    dst_ip = Column(String(15), nullable=False)
    src_port = Column(Integer, nullable=True)
    dst_port = Column(Integer, nullable=True)
    protocol = Column(String(10), nullable=False)
    bytes_sent = Column(Integer, default=0)
    bytes_received = Column(Integer, default=0)
    packets = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    flags = Column(JSON, nullable=True)

    __table_args__ = (
        Index('idx_flow_device_time', 'device_id', 'timestamp'),
        Index('idx_flow_dst_ip', 'dst_ip'),
    )


class Anomaly(Base):
    __tablename__ = "anomalies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    device_id = Column(Integer, nullable=False, index=True)
    timestamp = Column(DateTime, server_default=func.now(), index=True)
    anomaly_type = Column(String(50), nullable=False)
    severity = Column(String(10), nullable=False)
    score = Column(Float, nullable=False)
    description = Column(String(500), nullable=True)
    flow_ids = Column(JSON, nullable=True)
    features = Column(JSON, nullable=True)
    resolved = Column(Boolean, default=False)
    resolved_at = Column(DateTime, nullable=True)


class ModelMetadata(Base):
    __tablename__ = "model_metadata"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_type = Column(String(50), nullable=False)
    version = Column(String(20), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    training_samples = Column(Integer, default=0)
    accuracy = Column(Float, nullable=True)
    features_used = Column(JSON, nullable=True)
    parameters = Column(JSON, nullable=True)
    is_active = Column(Boolean, default=False)
