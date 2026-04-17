from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from ..core.database import get_db
from ..services.crud import AlertService
from ..models.schemas_pydantic import UnifiedAlertListResponse

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=UnifiedAlertListResponse)
async def list_alerts(
    limit: int = Query(50, ge=1, le=500),
    severity: Optional[str] = None,
    since_hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
):
    """Unified alert feed: IsolationForest anomalies + heuristic behavior alerts."""
    service = AlertService(db)
    alerts, total = await service.list_unified_alerts(
        limit=limit,
        severity=severity,
        since_hours=since_hours,
    )
    return UnifiedAlertListResponse(total=total, alerts=alerts)
