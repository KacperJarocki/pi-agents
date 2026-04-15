from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_, cast, Integer
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime, timedelta
from types import SimpleNamespace
from pathlib import Path
from ..models.schemas import Device, TrafficFlow, Anomaly, ModelMetadata
from ..models.schemas_pydantic import (
    DeviceCreate, DeviceUpdate, AnomalyCreate, AnomalyResolveRequest
)
from ..core.logging import log
from ..core.config import get_settings
from .gateway_control import GatewayAgentClient


class DeviceService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    async def _presence_maps(self) -> tuple[set[str], set[str], dict[str, str], list[dict]]:
        macs: set[str] = set()
        ips: set[str] = set()
        source_map: dict[str, str] = {}
        clients: list[dict] = []

        # Source 1: current DHCP leases from gateway-agent.
        try:
            code, body = await GatewayAgentClient().get_status()
            if code < 400 and isinstance(body, dict):
                for client in body.get("connected_clients") or []:
                    clients.append(client)
                    ip = (client.get("ip_address") or "").strip()
                    mac = (client.get("mac_address") or "").strip().lower()
                    if ip:
                        ips.add(ip)
                        source_map[f"ip:{ip}"] = "dhcp_lease"
                    if mac:
                        macs.add(mac)
                        source_map[f"mac:{mac}"] = "dhcp_lease"
        except Exception:
            # Fall back to DB-only view if agent is temporarily unavailable.
            pass

        return macs, ips, source_map, clients

    def _recently_seen(self, device: Device) -> bool:
        if not device.last_seen:
            return False
        cutoff = datetime.utcnow() - timedelta(minutes=self.settings.active_device_window_minutes)
        return device.last_seen >= cutoff

    def _decorate_device_presence(
        self,
        device: Device,
        present_macs: set[str],
        present_ips: set[str],
        source_map: dict[str, str],
    ) -> None:
        mac = (device.mac_address or "").lower()
        ip = device.ip_address or ""

        connected = False
        connection_source: str | None = None

        if mac and not mac.startswith("ip:") and mac in present_macs:
            connected = True
            connection_source = source_map.get(f"mac:{mac}", "dhcp_lease")
        elif ip and ip in present_ips:
            connected = True
            connection_source = source_map.get(f"ip:{ip}", "dhcp_lease")
        elif self._recently_seen(device):
            connected = True
            connection_source = "recent_traffic"

        model_status = "missing"
        if getattr(device, "id", None) and getattr(device, "id", 0) > 0:
            model_file = Path(self.settings.model_path) / f"isolation_forest_model_device_{device.id}.joblib"
            if model_file.exists():
                model_status = "ready"

        setattr(device, "connected", connected)
        setattr(device, "connection_source", connection_source)
        setattr(device, "model_status", model_status)

    def _synthetic_device(self, client: dict, idx: int):
        now = datetime.utcnow()
        return SimpleNamespace(
            id=-(idx + 1),
            mac_address=client.get("mac_address") or f"ip:{client.get('ip_address')}",
            ip_address=client.get("ip_address") or "0.0.0.0",
            hostname=client.get("hostname"),
            device_type=None,
            first_seen=now,
            last_seen=now,
            is_active=True,
            risk_score=0.0,
            extra_data=None,
            connected=True,
            connection_source="dhcp_lease",
            model_status="missing",
        )

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
        query = select(Device).order_by(desc(Device.risk_score))
        result = await self.db.execute(query)
        devices = list(result.scalars().all())

        present_macs, present_ips, source_map, clients = await self._presence_maps()
        for device in devices:
            self._decorate_device_presence(device, present_macs, present_ips, source_map)

        seen_keys = set()
        for device in devices:
            if device.mac_address and not device.mac_address.startswith("ip:"):
                seen_keys.add(f"mac:{device.mac_address.lower()}")
            if device.ip_address:
                seen_keys.add(f"ip:{device.ip_address}")

        synthetic = []
        for idx, client in enumerate(clients):
            mac = (client.get("mac_address") or "").lower()
            ip = client.get("ip_address") or ""
            if (mac and f"mac:{mac}" in seen_keys) or (ip and f"ip:{ip}" in seen_keys):
                continue
            synthetic.append(self._synthetic_device(client, idx))

        devices.extend(synthetic)

        if active_only:
            devices = [d for d in devices if getattr(d, "connected", False)]

        total = len(devices)
        devices = devices[skip:skip + limit]
        return devices, total

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
