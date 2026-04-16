from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from time import perf_counter
from ..core.database import get_db
from ..core.cache import cache
from ..core.config import get_settings
from ..services.crud import TrafficService, DeviceService, AnomalyService
from ..core.logging import log
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
    data = await cache.get_or_set(
        f"timeline:{hours}:{interval_minutes}",
        5.0,
        lambda: service.get_timeline_data(hours, interval_minutes),
    )
    return TimelineResponse(data=data)


@router.get("/top-talking", response_model=TopTalkersResponse)
async def get_top_talkers(
    limit: int = Query(10, ge=1, le=50),
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db)
):
    service = TrafficService(db)
    data = await cache.get_or_set(
        f"top-talking:{limit}:{hours}",
        5.0,
        lambda: service.get_top_talkers(limit, hours),
    )
    return TopTalkersResponse(data=data)


@router.get("/summary", response_model=MetricsSummary)
async def get_summary(
    db: AsyncSession = Depends(get_db)
):
    async def load_summary() -> MetricsSummary:
        started = perf_counter()
        device_service = DeviceService(db)
        anomaly_service = AnomalyService(db)
        total_devices_result = await db.execute(select(func.count()).select_from(Device))
        total_devices = int(total_devices_result.scalar() or 0)
        active_devices_count = await device_service.count_connected_devices()
        anomaly_stats = await anomaly_service.get_anomaly_stats(hours=24)

        traffic_total_result = await db.execute(
            select(func.sum(func.coalesce(func.json_extract(Device.extra_data, '$.total_bytes'), 0)))
        )
        total_traffic = float((traffic_total_result.scalar() or 0) / (1024 * 1024))

        avg_risk_result = await db.execute(select(func.avg(Device.risk_score)))
        avg_risk = float(avg_risk_result.scalar() or 0.0)

        summary = MetricsSummary(
            total_devices=int(total_devices or 0),
            active_devices=int(active_devices_count or 0),
            total_anomalies_24h=anomaly_stats["total"],
            critical_anomalies=anomaly_stats["critical"],
            avg_risk_score=avg_risk,
            total_traffic_mb=total_traffic
        )
        log.info("get_summary_timed", duration_ms=round((perf_counter() - started) * 1000, 2), total_devices=summary.total_devices)
        return summary

    return await cache.get_or_set("metrics-summary", 3.0, load_summary)


@router.get("/ml-status", response_model=MlStatusResponse)
async def get_ml_status(
    db: AsyncSession = Depends(get_db)
):
    settings = get_settings()
    device_service = DeviceService(db)
    devices, total = await cache.get_or_set(
        "ml-status",
        5.0,
        lambda: device_service.list_devices(limit=1000),
    )

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
