from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from ..core.database import get_db
from ..core.cache import cache
from ..services.crud import DeviceService, TrafficService, AnomalyService, InferenceHistoryService, BehaviorAlertService
from ..models.schemas_pydantic import (
    DeviceCreate, DeviceUpdate, DeviceResponse, DeviceListResponse,
    DeviceTrafficResponse, DeviceDestinationsResponse, DeviceInferenceHistoryResponse,
    AnomalyListResponse, DeviceBehaviorAlertListResponse, DeviceRiskContributorsResponse,
)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("", response_model=DeviceResponse, status_code=201)
async def create_device(
    device_data: DeviceCreate,
    db: AsyncSession = Depends(get_db)
):
    service = DeviceService(db)
    existing = await service.get_device_by_mac(device_data.mac_address)
    if existing:
        raise HTTPException(status_code=409, detail="Device with this MAC already exists")
    device = await service.create_device(device_data)
    return device


@router.get("", response_model=DeviceListResponse)
async def list_devices(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    active_only: bool = Query(False),
    db: AsyncSession = Depends(get_db)
):
    service = DeviceService(db)
    devices, total = await cache.get_or_set(
        f"devices:{skip}:{limit}:{int(active_only)}",
        3.0,
        lambda: service.list_devices(skip, limit, active_only),
    )
    return DeviceListResponse(total=total, devices=devices)


@router.get("/{device_id}/traffic", response_model=DeviceTrafficResponse)
async def get_device_traffic(
    device_id: int,
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db)
):
    device_service = DeviceService(db)
    device = await device_service.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    traffic_service = TrafficService(db)
    data = await cache.get_or_set(
        f"device-traffic:{device_id}:{hours}",
        5.0,
        lambda: traffic_service.get_device_traffic(device_id, hours=hours),
    )
    return DeviceTrafficResponse(device_id=device_id, hours=hours, data=data)


@router.get("/{device_id}/destinations", response_model=DeviceDestinationsResponse)
async def get_device_destinations(
    device_id: int,
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db)
):
    device_service = DeviceService(db)
    device = await device_service.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    traffic_service = TrafficService(db)
    destinations = await cache.get_or_set(
        f"device-destinations:{device_id}:{hours}",
        5.0,
        lambda: traffic_service.get_device_destinations(device_id, hours=hours),
    )
    ports = await cache.get_or_set(
        f"device-ports:{device_id}:{hours}",
        5.0,
        lambda: traffic_service.get_device_ports(device_id, hours=hours),
    )
    dns_queries = await cache.get_or_set(
        f"device-dns:{device_id}:{hours}",
        5.0,
        lambda: traffic_service.get_device_dns_queries(device_id, hours=hours),
    )
    return DeviceDestinationsResponse(
        device_id=device_id,
        destinations=destinations,
        ports=ports,
        dns_queries=dns_queries,
    )


@router.get("/{device_id}/anomalies", response_model=AnomalyListResponse)
async def get_device_anomalies(
    device_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    device_service = DeviceService(db)
    device = await device_service.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    anomaly_service = AnomalyService(db)
    anomalies, total = await cache.get_or_set(
        f"device-anomalies:{device_id}:{limit}",
        5.0,
        lambda: anomaly_service.list_anomalies(limit=limit, device_id=device_id),
    )
    return AnomalyListResponse(total=total, anomalies=anomalies)


@router.get("/{device_id}/inference-history", response_model=DeviceInferenceHistoryResponse)
async def get_device_inference_history(
    device_id: int,
    days: int = Query(7, ge=1, le=7),
    db: AsyncSession = Depends(get_db)
):
    device_service = DeviceService(db)
    device = await device_service.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    history_service = InferenceHistoryService(db)
    data = await cache.get_or_set(
        f"device-history:{device_id}:{days}",
        5.0,
        lambda: history_service.get_device_history(device_id, days=days),
    )
    return DeviceInferenceHistoryResponse(device_id=device_id, days=days, data=data)


@router.get("/{device_id}/behavior-alerts", response_model=DeviceBehaviorAlertListResponse)
async def get_device_behavior_alerts(
    device_id: int,
    limit: int = Query(20, ge=1, le=100),
    since_hours: int = Query(168, ge=1, le=168),
    db: AsyncSession = Depends(get_db)
):
    device_service = DeviceService(db)
    device = await device_service.get_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    alert_service = BehaviorAlertService(db)
    alerts, total = await cache.get_or_set(
        f"device-behavior-alerts:{device_id}:{limit}:{since_hours}",
        5.0,
        lambda: alert_service.list_device_alerts(device_id, limit=limit, since_hours=since_hours),
    )
    return DeviceBehaviorAlertListResponse(total=total, alerts=alerts)


@router.get("/{device_id}/risk-contributors", response_model=DeviceRiskContributorsResponse)
async def get_device_risk_contributors(
    device_id: int,
    db: AsyncSession = Depends(get_db)
):
    device_service = DeviceService(db)
    device = await device_service.get_decorated_device(device_id)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    alert_service = BehaviorAlertService(db)
    contributors = await cache.get_or_set(
        f"device-risk-contributors:{device_id}",
        5.0,
        lambda: alert_service.latest_risk_contributors(device_id),
    )
    if getattr(device, "last_inference_score", None) is not None:
        contributors = [
            {
                "contributor": "ml_inference",
                "severity": "critical" if device.risk_score >= 75 else "warning" if device.risk_score >= 35 else "info",
                "score": float(device.last_inference_score),
                "details": f"Latest model score {device.last_inference_score:.4f}",
            },
            *contributors,
        ]
    return DeviceRiskContributorsResponse(
        device_id=device_id,
        risk_score=float(device.risk_score or 0.0),
        contributors=contributors,
    )


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: int,
    db: AsyncSession = Depends(get_db)
):
    service = DeviceService(db)
    device = await cache.get_or_set(
        f"device:{device_id}",
        3.0,
        lambda: service.get_decorated_device(device_id),
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.patch("/{device_id}", response_model=DeviceResponse)
async def update_device(
    device_id: int,
    data: DeviceUpdate,
    db: AsyncSession = Depends(get_db)
):
    service = DeviceService(db)
    device = await service.update_device(device_id, data)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.put("/{device_id}/risk-score")
async def update_risk_score(
    device_id: int,
    score: float = Query(..., ge=0, le=100),
    db: AsyncSession = Depends(get_db)
):
    service = DeviceService(db)
    device = await service.update_risk_score(device_id, score)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return {"device_id": device_id, "risk_score": score}
