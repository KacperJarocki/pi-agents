from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from ..core.database import get_db
from ..services.crud import DeviceService
from ..models.schemas_pydantic import (
    DeviceCreate, DeviceUpdate, DeviceResponse, DeviceListResponse
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
    devices, total = await service.list_devices(skip, limit, active_only)
    return DeviceListResponse(total=total, devices=devices)


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(
    device_id: int,
    db: AsyncSession = Depends(get_db)
):
    service = DeviceService(db)
    device = await service.get_device(device_id)
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
