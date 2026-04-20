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
    source: Optional[str] = Query(
        None,
        description="Filter by alert source: 'anomaly' (ML models) or 'behavior' (heuristics). Omit for all.",
        pattern="^(anomaly|behavior)$",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Unified alert feed: ML anomalies + heuristic behavior alerts.

    Use the ``source`` query parameter to filter:
    - ``anomaly``  — only ML-generated anomalies (Isolation Forest, LOF, …)
    - ``behavior`` — only heuristic behavior alerts
    - omit        — both sources (default)
    """
    service = AlertService(db)
    alerts, total = await service.list_unified_alerts(
        limit=limit,
        severity=severity,
        since_hours=since_hours,
        source=source,
    )
    return UnifiedAlertListResponse(total=total, alerts=alerts)
