from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from time import perf_counter
import asyncio
from ..core.database import get_db
from ..core.cache import cache
from ..core.config import get_settings
from ..services.crud import TrafficService, DeviceService, AnomalyService, AlertService
from ..core.logging import log
from ..models.schemas import Device
from ..models.schemas_pydantic import (
    TimelineResponse, TopTalkersResponse, MetricsSummary, MlStatusResponse,
    DeviceModelStatus, ModelTrainingMetric,
)

router = APIRouter(prefix="/metrics", tags=["metrics"])

# Cache TTLs aligned with the dashboard 30-second poll interval.
# Previous values (3-5 s) caused ~8-10 unnecessary recomputations between polls.
_TTL_SUMMARY = 15.0   # metrics/summary  — expensive (DB + HTTP to gateway-agent)
_TTL_DEVICES = 10.0   # device list       — moderate cost
_TTL_HEAVY   = 15.0   # timeline / top-talking / ml-status — heavy SQL


@router.get("/timeline", response_model=TimelineResponse)
async def get_timeline(
    hours: int = Query(24, ge=1, le=168),
    interval_minutes: int = Query(60, ge=15, le=360),
    db: AsyncSession = Depends(get_db)
):
    service = TrafficService(db)
    data = await cache.get_or_set(
        f"timeline:{hours}:{interval_minutes}",
        _TTL_HEAVY,
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
        _TTL_HEAVY,
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

        # Sequential: total_devices first (cheap), then connected count (involves HTTP).
        total_devices_result = await db.execute(select(func.count()).select_from(Device))
        total_devices = int(total_devices_result.scalar() or 0)
        active_devices_count = await device_service.count_connected_devices()

        # Run independent DB aggregations concurrently.
        (
            anomaly_stats,
            behavior_stats,
            traffic_total_row,
            avg_risk_row,
        ) = await asyncio.gather(
            anomaly_service.get_anomaly_stats(hours=24),
            anomaly_service.behavior_alert_stats(hours=24),
            db.execute(
                select(func.sum(func.coalesce(func.json_extract(Device.extra_data, '$.total_bytes'), 0)))
            ),
            db.execute(select(func.avg(Device.risk_score))),
        )

        total_traffic = float((traffic_total_row.scalar() or 0) / (1024 * 1024))
        avg_risk = float(avg_risk_row.scalar() or 0.0)

        summary = MetricsSummary(
            total_devices=int(total_devices or 0),
            active_devices=int(active_devices_count or 0),
            total_anomalies_24h=anomaly_stats["total"],
            critical_anomalies=anomaly_stats["critical"],
            avg_risk_score=avg_risk,
            total_traffic_mb=total_traffic,
            behavior_alerts_24h=behavior_stats["total"],
            total_alerts_24h=anomaly_stats["total"] + behavior_stats["total"],
        )
        log.info("get_summary_timed", duration_ms=round((perf_counter() - started) * 1000, 2), total_devices=summary.total_devices)
        return summary

    return await cache.get_or_set("metrics-summary", _TTL_SUMMARY, load_summary)


@router.get("/ml-status", response_model=MlStatusResponse)
async def get_ml_status(
    db: AsyncSession = Depends(get_db)
):
    settings = get_settings()
    device_service = DeviceService(db)
    devices, total = await cache.get_or_set(
        "ml-status",
        _TTL_HEAVY,
        lambda: device_service.list_devices(limit=1000),
    )

    # Fetch latest training metrics per device from model_metadata (best-effort).
    # Returns empty dict if table does not yet exist (before first training run).
    training_metrics_by_device: dict[int, list[ModelTrainingMetric]] = {}
    model_health_available = False
    try:
        result = await db.execute(text(
            """
            SELECT device_id, model_type, trained_at, samples, threshold,
                   score_mean, score_std, score_p5, score_p95, estimated_anomaly_rate
            FROM model_metadata
            WHERE id IN (
                SELECT MAX(id) FROM model_metadata
                WHERE device_id IS NOT NULL
                GROUP BY device_id, model_type
            )
            ORDER BY device_id, model_type
            """
        ))
        rows = result.fetchall()
        for row in rows:
            did = int(row[0])
            training_metrics_by_device.setdefault(did, []).append(
                ModelTrainingMetric(
                    model_type=row[1],
                    trained_at=row[2],
                    samples=row[3],
                    threshold=row[4],
                    score_mean=row[5],
                    score_std=row[6],
                    score_p5=row[7],
                    score_p95=row[8],
                    estimated_anomaly_rate=row[9],
                )
            )
        model_health_available = bool(rows)
    except Exception as exc:
        # Gracefully degrade when model_metadata doesn't exist yet (first boot
        # before any training run) or on unexpected query errors.
        log.warning("model_metadata_query_failed", error=str(exc))

    statuses = [
        DeviceModelStatus(
            device_id=int(device.id),
            model_status=getattr(device, "model_status", "missing"),
            last_inference_score=getattr(device, "last_inference_score", None),
            last_inference_at=getattr(device, "last_inference_at", None),
            training_metrics=training_metrics_by_device.get(int(device.id)),
        )
        for device in devices
        if getattr(device, "id", 0) > 0
    ]

    ready_count = sum(1 for s in statuses if s.model_status == "ready")

    return MlStatusResponse(
        model_path=settings.model_path,
        device_models_ready=ready_count,
        total_devices=int(total or 0),
        model_health_available=model_health_available,
        devices=statuses,
    )
