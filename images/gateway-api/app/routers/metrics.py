from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pathlib import Path
from ..core.database import get_db
from ..core.config import get_settings
from ..services.crud import TrafficService, DeviceService, AnomalyService
from ..models.schemas import Device
from ..models.schemas_pydantic import (
    TimelineResponse, TopTalkersResponse, MetricsSummary, MlStatusResponse, DeviceModelStatus
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
    
    _, total_devices = await device_service.list_devices(limit=1)
    _, active_devices_count = await device_service.list_devices(active_only=True, limit=1)
    
    anomaly_stats = await anomaly_service.get_anomaly_stats(hours=24)
    top_talkers = await traffic_service.get_top_talkers(limit=100, hours=24)
    total_traffic = sum(t.get("total_bytes", 0) for t in top_talkers) / (1024 * 1024)

    avg_risk_result = await db.execute(select(func.avg(Device.risk_score)))
    avg_risk = float(avg_risk_result.scalar() or 0.0)
    
    return MetricsSummary(
        total_devices=int(total_devices or 0),
        active_devices=int(active_devices_count or 0),
        total_anomalies_24h=anomaly_stats["total"],
        critical_anomalies=anomaly_stats["critical"],
        avg_risk_score=avg_risk,
        total_traffic_mb=total_traffic
    )


@router.get("/ml-status", response_model=MlStatusResponse)
async def get_ml_status(
    db: AsyncSession = Depends(get_db)
):
    settings = get_settings()
    device_service = DeviceService(db)
    devices, total = await device_service.list_devices(limit=1000)

    statuses = [
        DeviceModelStatus(
            device_id=int(device.id),
            model_status=getattr(device, "model_status", "missing"),
            last_inference_score=getattr(device, "last_inference_score", None),
            last_inference_at=getattr(device, "last_inference_at", None),
        )
        for device in devices
        if getattr(device, "id", 0) > 0
    ]

    ready_count = sum(1 for s in statuses if s.model_status == "ready")

    return MlStatusResponse(
        model_path=settings.model_path,
        device_models_ready=ready_count,
        total_devices=int(total or 0),
        devices=statuses,
    )
