from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class DeviceBase(BaseModel):
    mac_address: str
    ip_address: str
    hostname: Optional[str] = None
    device_type: Optional[str] = None


class DeviceCreate(DeviceBase):
    pass


class DeviceUpdate(BaseModel):
    hostname: Optional[str] = None
    device_type: Optional[str] = None
    is_active: Optional[bool] = None
    risk_score: Optional[float] = None


class DeviceResponse(DeviceBase):
    id: int
    first_seen: datetime
    last_seen: datetime
    is_active: bool
    risk_score: float
    last_inference_score: Optional[float] = None
    last_inference_at: Optional[datetime] = None
    extra_data: Optional[dict] = None
    connected: bool = False
    connection_source: Optional[str] = None
    model_status: Optional[str] = None

    class Config:
        from_attributes = True


class DeviceListResponse(BaseModel):
    total: int
    devices: List[DeviceResponse]


class TrafficFlowBase(BaseModel):
    device_id: int
    src_ip: str
    dst_ip: str
    src_port: Optional[int] = None
    dst_port: Optional[int] = None
    protocol: str
    bytes_sent: int = 0
    bytes_received: int = 0
    packets: int = 0
    duration_ms: int = 0
    dns_query: Optional[str] = None
    flags: Optional[dict] = None


class TrafficFlowCreate(TrafficFlowBase):
    pass


class TrafficFlowResponse(TrafficFlowBase):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True


class AnomalyBase(BaseModel):
    device_id: int
    anomaly_type: str
    severity: str
    score: float
    description: Optional[str] = None


class AnomalyCreate(AnomalyBase):
    flow_ids: Optional[List[int]] = None
    features: Optional[dict] = None


class AnomalyResponse(AnomalyBase):
    id: int
    timestamp: datetime
    flow_ids: Optional[List[int]] = None
    features: Optional[dict] = None
    resolved: bool
    resolved_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AnomalyListResponse(BaseModel):
    total: int
    anomalies: List[AnomalyResponse]


class AnomalyResolveRequest(BaseModel):
    resolved: bool = True


class TimelineDataPoint(BaseModel):
    timestamp: datetime
    anomaly_count: int
    total_traffic_mb: float
    active_devices: int


class TimelineResponse(BaseModel):
    data: List[TimelineDataPoint]


class TopTalker(BaseModel):
    ip_address: str
    hostname: Optional[str] = None
    total_bytes: int
    connection_count: int
    suspicious: bool = False


class TopTalkersResponse(BaseModel):
    data: List[TopTalker]


class UnifiedAlertItem(BaseModel):
    source: str  # 'isolation_forest' | 'behavior'
    id: int
    device_id: int
    device_hostname: Optional[str] = None
    device_ip: Optional[str] = None
    alert_type: str
    title: Optional[str] = None
    severity: str
    score: float
    timestamp: datetime
    resolved: bool


class UnifiedAlertListResponse(BaseModel):
    total: int
    alerts: List[UnifiedAlertItem]


class MetricsSummary(BaseModel):
    total_devices: int
    active_devices: int
    total_anomalies_24h: int
    critical_anomalies: int
    avg_risk_score: float
    total_traffic_mb: float
    behavior_alerts_24h: int = 0
    total_alerts_24h: int = 0


class ModelTrainingMetric(BaseModel):
    """Training observability record for a single model type and device."""

    model_type: str
    trained_at: Optional[str] = None
    samples: Optional[int] = None
    threshold: Optional[float] = None
    score_mean: Optional[float] = None
    score_std: Optional[float] = None
    score_p5: Optional[float] = None
    score_p95: Optional[float] = None
    estimated_anomaly_rate: Optional[float] = None


class DeviceModelStatus(BaseModel):
    device_id: int
    model_status: str
    last_inference_score: Optional[float] = None
    last_inference_at: Optional[datetime] = None
    training_metrics: Optional[List["ModelTrainingMetric"]] = None


class MlStatusResponse(BaseModel):
    model_path: str
    device_models_ready: int
    total_devices: int
    model_health_available: bool = False
    devices: List[DeviceModelStatus]


class DeviceTrafficPoint(BaseModel):
    timestamp: datetime
    total_traffic_mb: float
    packets: int
    unique_destinations: int


class DeviceTrafficResponse(BaseModel):
    device_id: int
    hours: int
    data: List[DeviceTrafficPoint]


class DeviceInferenceHistoryPoint(BaseModel):
    timestamp: datetime
    bucket_start: Optional[datetime] = None
    anomaly_score: float
    risk_score: float
    is_anomaly: bool
    severity: str
    features: Optional[dict] = None

    class Config:
        from_attributes = True


class DeviceInferenceHistoryResponse(BaseModel):
    device_id: int
    days: int
    data: List[DeviceInferenceHistoryPoint]


class DeviceDestination(BaseModel):
    value: str
    total_bytes: int
    connection_count: int


class DeviceDestinationsResponse(BaseModel):
    device_id: int
    destinations: List[DeviceDestination]
    ports: List[DeviceDestination]
    dns_queries: List[DeviceDestination]


class DeviceBehaviorAlertResponse(BaseModel):
    id: int
    device_id: int
    timestamp: datetime
    bucket_start: Optional[datetime] = None
    alert_type: str
    severity: str
    score: float
    title: str
    description: Optional[str] = None
    evidence: Optional[dict] = None
    resolved: bool

    class Config:
        from_attributes = True


class DeviceBehaviorAlertListResponse(BaseModel):
    total: int
    alerts: List[DeviceBehaviorAlertResponse]


class RiskContributor(BaseModel):
    contributor: str
    category: str
    severity: str
    score: float
    raw_score: float
    effective_score: float
    weight: float
    details: str
    reason: str
    last_seen: Optional[datetime] = None


class DeviceRiskContributorsResponse(BaseModel):
    device_id: int
    risk_score: float
    previous_risk_score: Optional[float] = None
    risk_delta: float = 0.0
    status: str
    ml_risk: float = 0.0
    behavior_risk: float = 0.0
    protocol_risk: float = 0.0
    correlation_bonus: float = 0.0
    top_reason: str
    latest_bucket_start: Optional[datetime] = None
    contributors: List[RiskContributor]


class BehaviorBaselineMetric(BaseModel):
    metric: str
    median: float
    p95: float
    latest: Optional[float] = None


class DeviceBehaviorBaselineResponse(BaseModel):
    device_id: int
    days: int
    metrics: List[BehaviorBaselineMetric]


class ProtocolSignal(BaseModel):
    label: str
    value: float
    note: str


class DeviceProtocolSignalsResponse(BaseModel):
    device_id: int
    hours: int
    signals: List[ProtocolSignal]


class HealthResponse(BaseModel):
    status: str
    database: str
    timestamp: datetime


class WifiConfig(BaseModel):
    ssid: str
    psk: str
    country_code: str = "PL"
    channel: int = 6
    ap_interface: str = "wlan0"
    upstream_interface: str = "eth0"
    subnet_cidr: str = "192.168.50.0/24"
    gateway_ip: str = "192.168.50.1"
    dhcp_range_start: str = "192.168.50.100"
    dhcp_range_end: str = "192.168.50.200"
    enabled: bool = True


class WifiConfigResponse(BaseModel):
    config: WifiConfig


class WifiValidationResponse(BaseModel):
    ok: bool
    issues: List[str] = []


class GatewayAgentStatusResponse(BaseModel):
    status: dict


class WifiApplyResponse(BaseModel):
    ok: bool
    message: str


class ModelScoreEntry(BaseModel):
    model_type: str
    anomaly_score: float
    risk_score: float
    is_anomaly: bool
    timestamp: Optional[datetime] = None
    bucket_start: Optional[datetime] = None


class ModelScoreHistoryPoint(BaseModel):
    timestamp: datetime
    bucket_start: Optional[datetime] = None
    anomaly_score: float
    risk_score: float
    is_anomaly: bool


class DeviceModelScoresResponse(BaseModel):
    device_id: int
    hours: int
    model_type: str
    data: List[ModelScoreHistoryPoint]


class DeviceModelConfigResponse(BaseModel):
    device_id: int
    model_type: str
    params: Optional[dict] = None
    available_models: List[ModelScoreEntry] = []


class DeviceModelConfigUpdate(BaseModel):
    model_type: str = Field(..., pattern=r"^(isolation_forest|lof|ocsvm|autoencoder)$")


# ── Training configuration (Faza 3) ─────────────────────────────────────────

class TrainingConfigResponse(BaseModel):
    """Global or effective (merged) training configuration."""
    training_hours: int = 48
    min_training_samples: int = 10
    contamination: float = 0.05
    n_estimators: int = 200
    feature_bucket_minutes: int = 5
    per_device_models: bool = True
    updated_at: Optional[str] = None
    # Only present in effective (merged) responses
    device_id: Optional[int] = None
    has_overrides: Optional[bool] = None


class TrainingConfigUpdate(BaseModel):
    """Partial update for training configuration (all fields optional)."""
    training_hours: Optional[int] = Field(None, ge=1, le=720)
    min_training_samples: Optional[int] = Field(None, ge=1, le=10000)
    contamination: Optional[float] = Field(None, ge=0.01, le=0.5)
    n_estimators: Optional[int] = Field(None, ge=10, le=2000)
    feature_bucket_minutes: Optional[int] = Field(None, ge=1, le=60)
    per_device_models: Optional[bool] = None


class DeviceTrainingConfigResponse(BaseModel):
    """Per-device training config overrides (NULL = use global default)."""
    device_id: int
    training_hours: Optional[int] = None
    min_training_samples: Optional[int] = None
    contamination: Optional[float] = None
    n_estimators: Optional[int] = None
    feature_bucket_minutes: Optional[int] = None
    updated_at: Optional[str] = None


class DeviceTrainingConfigUpdate(BaseModel):
    """Partial update for per-device training config. Set a field to null to clear override."""
    training_hours: Optional[int] = Field(None, ge=1, le=720)
    min_training_samples: Optional[int] = Field(None, ge=1, le=10000)
    contamination: Optional[float] = Field(None, ge=0.01, le=0.5)
    n_estimators: Optional[int] = Field(None, ge=10, le=2000)
    feature_bucket_minutes: Optional[int] = Field(None, ge=1, le=60)
