from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from ..core.database import get_db
from ..core.logging import log
from ..models.schemas import GatewayWifiConfig
from ..models.schemas_pydantic import WifiConfig, WifiConfigResponse, WifiValidationResponse, WifiApplyResponse
from ..services.gateway_control import GatewayAgentClient


router = APIRouter(prefix="/gateway/wifi", tags=["gateway"])


async def _get_or_create_row(db: AsyncSession) -> GatewayWifiConfig:
    res = await db.execute(select(GatewayWifiConfig).order_by(GatewayWifiConfig.id.asc()).limit(1))
    row = res.scalar_one_or_none()
    if row:
        return row
    row = GatewayWifiConfig(config=WifiConfig(ssid="IoT-Security", psk="change-me-please").model_dump())
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


@router.get("/config", response_model=WifiConfigResponse)
async def get_config(db: AsyncSession = Depends(get_db)):
    row = await _get_or_create_row(db)
    return WifiConfigResponse(config=WifiConfig(**row.config))


@router.put("/config", response_model=WifiConfigResponse)
async def put_config(cfg: WifiConfig, db: AsyncSession = Depends(get_db)):
    row = await _get_or_create_row(db)
    row.config = cfg.model_dump()
    await db.flush()
    await db.refresh(row)
    log.info("gateway_wifi_config_updated")
    return WifiConfigResponse(config=cfg)


@router.post("/validate", response_model=WifiValidationResponse)
async def validate_config(cfg: WifiConfig, db: AsyncSession = Depends(get_db)):
    client = GatewayAgentClient()
    try:
        res = await client.validate(cfg.model_dump())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"gateway-agent validate failed: {e}")
    return WifiValidationResponse(ok=bool(res.get("ok")), issues=res.get("issues") or [])


@router.get("/status")
async def status():
    client = GatewayAgentClient()
    try:
        return await client.get_status()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"gateway-agent status failed: {e}")


@router.post("/apply", response_model=WifiApplyResponse)
async def apply(cfg: WifiConfig, db: AsyncSession = Depends(get_db)):
    # Persist desired config first.
    row = await _get_or_create_row(db)
    row.config = cfg.model_dump()

    client = GatewayAgentClient()
    try:
        res = await client.apply(cfg.model_dump())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"gateway-agent apply failed: {e}")

    row.last_apply_ok = bool(res.get("ok"))
    row.last_apply_message = str(res.get("message"))
    row.last_apply_at = datetime.utcnow()
    await db.flush()
    await db.refresh(row)

    return WifiApplyResponse(ok=row.last_apply_ok, message=row.last_apply_message or "")


@router.post("/rollback", response_model=WifiApplyResponse)
async def rollback(db: AsyncSession = Depends(get_db)):
    row = await _get_or_create_row(db)

    client = GatewayAgentClient()
    try:
        res = await client.rollback()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"gateway-agent rollback failed: {e}")

    row.last_apply_ok = bool(res.get("ok"))
    row.last_apply_message = str(res.get("message"))
    row.last_apply_at = datetime.utcnow()
    await db.flush()
    await db.refresh(row)

    return WifiApplyResponse(ok=row.last_apply_ok, message=row.last_apply_message or "")
