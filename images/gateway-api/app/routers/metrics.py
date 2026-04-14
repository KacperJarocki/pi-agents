from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from ..core.database import get_db
from ..services.crud import TrafficService, DeviceService, AnomalyService
from ..models.schemas_pydantic import (
    TimelineResponse, TopTalkersResponse, MetricsSummary
)

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/timeline", response_model=TimelineResponse)
async def get_timeline(
    hours: int = Query(24, ge=1, le=168),
    interval_minutes: int = Query(60, ge=15, le=360),
    db: AsyncSession = Depends(get_db)
):
    service = TrafficService(db)
    data = await service.get_timeline_data(hours, interval_minutes)
    return TimelineResponse(data=data)


@router.get("/top-talking", response_model=TopTalkersResponse)
async def get_top_talkers(
    limit: int = Query(10, ge=1, le=50),
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db)
):
    service = TrafficService(db)
    data = await service.get_top_talkers(limit, hours)
    return TopTalkersResponse(data=data)


@router.get("/summary", response_model=MetricsSummary)
async def get_summary(
    db: AsyncSession = Depends(get_db)
):
    device_service = DeviceService(db)
    anomaly_service = AnomalyService(db)
    traffic_service = TrafficService(db)
    
    all_devices, _ = await device_service.list_devices(limit=1)
    active_devices, _ = await device_service.list_devices(active_only=True, limit=1)
    
    anomaly_stats = await anomaly_service.get_anomaly_stats(hours=24)
    top_talkers = await traffic_service.get_top_talkers(limit=100, hours=24)
    total_traffic = sum(t.get("total_bytes", 0) for t in top_talkers) / (1024 * 1024)
    
    avg_risk = sum(d.risk_score for d in all_devices) / len(all_devices) if all_devices else 0.0
    
    return MetricsSummary(
        total_devices=len(all_devices),
        active_devices=len(active_devices),
        total_anomalies_24h=anomaly_stats["total"],
        critical_anomalies=anomaly_stats["critical"],
        avg_risk_score=avg_risk,
        total_traffic_mb=total_traffic
    )
