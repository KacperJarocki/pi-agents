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


class MetricsSummary(BaseModel):
    total_devices: int
    active_devices: int
    total_anomalies_24h: int
    critical_anomalies: int
    avg_risk_score: float
    total_traffic_mb: float


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
