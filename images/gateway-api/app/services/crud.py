from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_, cast, Integer
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime, timedelta
from ..models.schemas import Device, TrafficFlow, Anomaly, ModelMetadata
from ..models.schemas_pydantic import (
    DeviceCreate, DeviceUpdate, AnomalyCreate, AnomalyResolveRequest
)
from ..core.logging import log


class DeviceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_device(self, device_data: DeviceCreate) -> Device:
        device = Device(**device_data.model_dump())
        self.db.add(device)
        await self.db.flush()
        await self.db.refresh(device)
        log.info("device_created", device_id=device.id, mac=device.mac_address)
        return device

    async def get_device(self, device_id: int) -> Optional[Device]:
        result = await self.db.execute(
            select(Device).where(Device.id == device_id)
        )
        return result.scalar_one_or_none()

    async def get_device_by_mac(self, mac_address: str) -> Optional[Device]:
        result = await self.db.execute(
            select(Device).where(Device.mac_address == mac_address)
        )
        return result.scalar_one_or_none()

    async def list_devices(self, skip: int = 0, limit: int = 100, 
                          active_only: bool = False) -> tuple[List[Device], int]:
        query = select(Device)
        if active_only:
            query = query.where(Device.is_active == True)
        
        count_query = select(func.count()).select_from(Device)
        if active_only:
            count_query = count_query.where(Device.is_active == True)
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        query = query.order_by(desc(Device.risk_score)).offset(skip).limit(limit)
        result = await self.db.execute(query)
        devices = result.scalars().all()
        
        return list(devices), total

    async def update_device(self, device_id: int, data: DeviceUpdate) -> Optional[Device]:
        device = await self.get_device(device_id)
        if not device:
            return None
        
        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(device, key, value)
        
        await self.db.flush()
        await self.db.refresh(device)
        log.info("device_updated", device_id=device_id)
        return device

    async def update_risk_score(self, device_id: int, score: float) -> Optional[Device]:
        device = await self.get_device(device_id)
        if device:
            device.risk_score = score
            await self.db.flush()
            await self.db.refresh(device)
        return device


class AnomalyService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_anomaly(self, anomaly_data: AnomalyCreate) -> Anomaly:
        anomaly = Anomaly(**anomaly_data.model_dump())
        self.db.add(anomaly)
        await self.db.flush()
        await self.db.refresh(anomaly)
        log.warning("anomaly_detected", 
                   anomaly_id=anomaly.id, 
                   device_id=anomaly.device_id,
                   type=anomaly.anomaly_type,
                   score=anomaly.score)
        return anomaly

    async def get_anomaly(self, anomaly_id: int) -> Optional[Anomaly]:
        result = await self.db.execute(
            select(Anomaly).where(Anomaly.id == anomaly_id)
        )
        return result.scalar_one_or_none()

    async def list_anomalies(self, skip: int = 0, limit: int = 100,
                            device_id: Optional[int] = None,
                            severity: Optional[str] = None,
                            resolved: Optional[bool] = None,
                            since: Optional[datetime] = None) -> tuple[List[Anomaly], int]:
        query = select(Anomaly)
        count_query = select(func.count()).select_from(Anomaly)
        
        filters = []
        if device_id:
            filters.append(Anomaly.device_id == device_id)
        if severity:
            filters.append(Anomaly.severity == severity)
        if resolved is not None:
            filters.append(Anomaly.resolved == resolved)
        if since:
            filters.append(Anomaly.timestamp >= since)
        
        if filters:
            query = query.where(and_(*filters))
            count_query = count_query.where(and_(*filters))
        
        total_result = await self.db.execute(count_query)
        total = total_result.scalar()
        
        query = query.order_by(desc(Anomaly.timestamp)).offset(skip).limit(limit)
        result = await self.db.execute(query)
        anomalies = result.scalars().all()
        
        return list(anomalies), total

    async def resolve_anomaly(self, anomaly_id: int, 
                             resolve_data: AnomalyResolveRequest) -> Optional[Anomaly]:
        anomaly = await self.get_anomaly(anomaly_id)
        if not anomaly:
            return None
        
        anomaly.resolved = resolve_data.resolved
        if resolve_data.resolved:
            anomaly.resolved_at = datetime.utcnow()
        
        await self.db.flush()
        await self.db.refresh(anomaly)
        log.info("anomaly_resolved", anomaly_id=anomaly_id, resolved=resolve_data.resolved)
        return anomaly

    async def get_anomaly_stats(self, hours: int = 24) -> dict:
        since = datetime.utcnow() - timedelta(hours=hours)
        
        total_query = select(func.count()).select_from(Anomaly).where(
            Anomaly.timestamp >= since
        )
        total_result = await self.db.execute(total_query)
        total = total_result.scalar()
        
        critical_query = select(func.count()).select_from(Anomaly).where(
            and_(Anomaly.timestamp >= since, Anomaly.severity == "critical")
        )
        critical_result = await self.db.execute(critical_query)
        critical = critical_result.scalar()
        
        return {"total": total, "critical": critical}


class TrafficService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_flow(self, flow_data: dict) -> TrafficFlow:
        flow = TrafficFlow(**flow_data)
        self.db.add(flow)
        await self.db.flush()
        await self.db.refresh(flow)
        return flow

    async def get_timeline_data(self, hours: int = 24, interval_minutes: int = 60) -> List[dict]:
        since = datetime.utcnow() - timedelta(hours=hours)

        bucket_size = max(15, int(interval_minutes)) * 60
        flow_epoch = cast(func.strftime('%s', TrafficFlow.timestamp), Integer)
        flow_bucket_epoch = cast(flow_epoch / bucket_size, Integer) * bucket_size
        flow_bucket = func.datetime(flow_bucket_epoch, 'unixepoch').label('bucket')

        anom_epoch = cast(func.strftime('%s', Anomaly.timestamp), Integer)
        anom_bucket_epoch = cast(anom_epoch / bucket_size, Integer) * bucket_size
        anom_bucket = func.datetime(anom_bucket_epoch, 'unixepoch')

        query = (
            select(
                flow_bucket,
                func.count(func.distinct(Anomaly.id)).label('anomaly_count'),
                func.sum(TrafficFlow.bytes_sent + TrafficFlow.bytes_received).label('total_traffic'),
                func.count(func.distinct(TrafficFlow.device_id)).label('active_devices'),
            )
            .outerjoin(Anomaly, anom_bucket == flow_bucket)
            .where(TrafficFlow.timestamp >= since)
            .group_by(flow_bucket)
            .order_by(flow_bucket)
        )

        result = await self.db.execute(query)
        data = []
        for row in result:
            # row.bucket is a SQLite datetime string: "YYYY-MM-DD HH:MM:SS"
            ts = row.bucket.replace(' ', 'T') + 'Z' if row.bucket else None
            data.append(
                {
                    "timestamp": ts,
                    "anomaly_count": int(row.anomaly_count or 0),
                    "total_traffic_mb": float((row.total_traffic or 0) / (1024 * 1024)),
                    "active_devices": int(row.active_devices or 0),
                }
            )
        return data

    async def get_top_talkers(self, limit: int = 10, hours: int = 24) -> List[dict]:
        since = datetime.utcnow() - timedelta(hours=hours)
        
        query = select(
            TrafficFlow.dst_ip,
            func.sum(TrafficFlow.bytes_sent + TrafficFlow.bytes_received).label('total_bytes'),
            func.count(TrafficFlow.id).label('connection_count'),
        ).where(
            TrafficFlow.timestamp >= since
        ).group_by(
            TrafficFlow.dst_ip
        ).order_by(
            desc('total_bytes')
        ).limit(limit)
        
        result = await self.db.execute(query)
        return [{"ip_address": row.dst_ip, "total_bytes": row.total_bytes,
                 "connection_count": row.connection_count} for row in result]
