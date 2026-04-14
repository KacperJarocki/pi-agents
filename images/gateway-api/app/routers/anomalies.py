from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime
from ..core.database import get_db
from ..services.crud import AnomalyService
from ..models.schemas_pydantic import (
    AnomalyCreate, AnomalyResponse, AnomalyListResponse, AnomalyResolveRequest
)

router = APIRouter(prefix="/anomalies", tags=["anomalies"])


@router.post("", response_model=AnomalyResponse, status_code=201)
async def create_anomaly(
    anomaly_data: AnomalyCreate,
    db: AsyncSession = Depends(get_db)
):
    service = AnomalyService(db)
    anomaly = await service.create_anomaly(anomaly_data)
    return anomaly


@router.get("", response_model=AnomalyListResponse)
async def list_anomalies(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    device_id: Optional[int] = None,
    severity: Optional[str] = None,
    resolved: Optional[bool] = None,
    since: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db)
):
    service = AnomalyService(db)
    anomalies, total = await service.list_anomalies(
        skip, limit, device_id, severity, resolved, since
    )
    return AnomalyListResponse(total=total, anomalies=anomalies)


@router.get("/{anomaly_id}", response_model=AnomalyResponse)
async def get_anomaly(
    anomaly_id: int,
    db: AsyncSession = Depends(get_db)
):
    service = AnomalyService(db)
    anomaly = await service.get_anomaly(anomaly_id)
    if not anomaly:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    return anomaly


@router.patch("/{anomaly_id}/resolve", response_model=AnomalyResponse)
async def resolve_anomaly(
    anomaly_id: int,
    resolve_data: AnomalyResolveRequest,
    db: AsyncSession = Depends(get_db)
):
    service = AnomalyService(db)
    anomaly = await service.resolve_anomaly(anomaly_id, resolve_data)
    if not anomaly:
        raise HTTPException(status_code=404, detail="Anomaly not found")
    return anomaly
