from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_, cast, Integer, text
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime, timedelta
from types import SimpleNamespace
from pathlib import Path
from time import perf_counter
import json
from ..models.schemas import Device, TrafficFlow, Anomaly, DeviceInferenceHistory, DeviceBehaviorAlert, ModelMetadata
from ..models.schemas_pydantic import (
    DeviceCreate, DeviceUpdate, AnomalyCreate, AnomalyResolveRequest
)
from ..core.logging import log
from ..core.config import get_settings
from .gateway_control import GatewayAgentClient


PROTOCOL_ALERT_TYPES = {
    "dns_failure_spike",
    "dns_nxdomain_burst",
    "icmp_sweep_suspected",
    "icmp_echo_fanout",
}


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
        started = perf_counter()

        if active_only:
            query = select(Device).order_by(desc(Device.risk_score), desc(Device.last_seen))
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
            devices = [d for d in devices if getattr(d, "connected", False)]
            total = len(devices)
            log.info("list_devices_timed", active_only=active_only, total=total, duration_ms=round((perf_counter() - started) * 1000, 2))
            return devices[skip:skip + limit], total

        count_result = await self.db.execute(select(func.count()).select_from(Device))
        db_total = int(count_result.scalar() or 0)
        query = (
            select(Device)
            .order_by(desc(Device.risk_score), desc(Device.last_seen))
            .offset(skip)
            .limit(limit)
        )
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
        synthetic_total = 0
        for idx, client in enumerate(clients):
            mac = (client.get("mac_address") or "").lower()
            ip = client.get("ip_address") or ""
            if (mac and f"mac:{mac}" in seen_keys) or (ip and f"ip:{ip}" in seen_keys):
                continue
            synthetic_total += 1
            if skip == 0 and len(devices) + len(synthetic) < limit:
                synthetic.append(self._synthetic_device(client, idx))

        devices.extend(synthetic)
        total = db_total + synthetic_total
        log.info("list_devices_timed", active_only=active_only, total=total, duration_ms=round((perf_counter() - started) * 1000, 2))
        return devices, total

    async def count_connected_devices(self) -> int:
        started = perf_counter()
        devices, _ = await self.list_devices(limit=1000, active_only=True)
        count = len(devices)
        log.info("count_connected_devices_timed", count=count, duration_ms=round((perf_counter() - started) * 1000, 2))
        return count

    async def get_decorated_device(self, device_id: int) -> Optional[Device]:
        device = await self.get_device(device_id)
        if not device:
            return None
        present_macs, present_ips, source_map, _ = await self._presence_maps()
        self._decorate_device_presence(device, present_macs, present_ips, source_map)
        return device

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

    async def behavior_alert_stats(self, hours: int = 24) -> dict:
        since = f"-{hours} hours"
        sql = text("""
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN severity = 'critical' THEN 1 ELSE 0 END) AS critical
            FROM device_behavior_alerts
            WHERE timestamp >= datetime('now', :since)
        """)
        result = await self.db.execute(sql, {"since": since})
        row = result.fetchone()
        return {"total": int(row.total or 0), "critical": int(row.critical or 0)}


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
        started = perf_counter()
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
        log.info("get_timeline_data_timed", hours=hours, points=len(data), duration_ms=round((perf_counter() - started) * 1000, 2))
        return data

    async def get_top_talkers(self, limit: int = 10, hours: int = 24) -> List[dict]:
        started = perf_counter()
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
        data = [{"ip_address": row.dst_ip, "total_bytes": row.total_bytes,
                 "connection_count": row.connection_count} for row in result]
        log.info("get_top_talkers_timed", hours=hours, limit=limit, rows=len(data), duration_ms=round((perf_counter() - started) * 1000, 2))
        return data

    async def get_device_traffic(self, device_id: int, hours: int = 24, interval_minutes: int = 5) -> List[dict]:
        bucket_size = max(5, int(interval_minutes)) * 60
        since = datetime.utcnow() - timedelta(hours=hours)
        flow_epoch = cast(func.strftime('%s', TrafficFlow.timestamp), Integer)
        flow_bucket_epoch = cast(flow_epoch / bucket_size, Integer) * bucket_size
        flow_bucket = func.datetime(flow_bucket_epoch, 'unixepoch').label('bucket')

        query = (
            select(
                flow_bucket,
                func.sum(TrafficFlow.bytes_sent + TrafficFlow.bytes_received).label('total_traffic'),
                func.count(TrafficFlow.id).label('packets'),
                func.count(func.distinct(TrafficFlow.dst_ip)).label('unique_destinations'),
            )
            .where(and_(TrafficFlow.device_id == device_id, TrafficFlow.timestamp >= since))
            .group_by(flow_bucket)
            .order_by(flow_bucket)
        )
        result = await self.db.execute(query)
        return [
            {
                "timestamp": row.bucket.replace(' ', 'T') + 'Z',
                "total_traffic_mb": float((row.total_traffic or 0) / (1024 * 1024)),
                "packets": int(row.packets or 0),
                "unique_destinations": int(row.unique_destinations or 0),
            }
            for row in result
            if row.bucket
        ]

    async def get_device_destinations(self, device_id: int, hours: int = 24, limit: int = 10) -> List[dict]:
        since = datetime.utcnow() - timedelta(hours=hours)
        query = (
            select(
                TrafficFlow.dst_ip.label('value'),
                func.sum(TrafficFlow.bytes_sent + TrafficFlow.bytes_received).label('total_bytes'),
                func.count(TrafficFlow.id).label('connection_count'),
            )
            .where(and_(TrafficFlow.device_id == device_id, TrafficFlow.timestamp >= since))
            .group_by(TrafficFlow.dst_ip)
            .order_by(desc('total_bytes'))
            .limit(limit)
        )
        result = await self.db.execute(query)
        return [dict(row._mapping) for row in result]

    async def get_device_ports(self, device_id: int, hours: int = 24, limit: int = 10) -> List[dict]:
        since = datetime.utcnow() - timedelta(hours=hours)
        query = (
            select(
                cast(TrafficFlow.dst_port, Integer).label('value'),
                func.sum(TrafficFlow.bytes_sent + TrafficFlow.bytes_received).label('total_bytes'),
                func.count(TrafficFlow.id).label('connection_count'),
            )
            .where(and_(TrafficFlow.device_id == device_id, TrafficFlow.timestamp >= since))
            .group_by(TrafficFlow.dst_port)
            .order_by(desc('total_bytes'))
            .limit(limit)
        )
        result = await self.db.execute(query)
        return [
            {
                "value": str(row.value or 0),
                "total_bytes": int(row.total_bytes or 0),
                "connection_count": int(row.connection_count or 0),
            }
            for row in result
        ]

    async def get_device_dns_queries(self, device_id: int, hours: int = 24, limit: int = 10) -> List[dict]:
        since = datetime.utcnow() - timedelta(hours=hours)
        query = (
            select(
                TrafficFlow.dns_query.label('value'),
                func.sum(TrafficFlow.bytes_sent + TrafficFlow.bytes_received).label('total_bytes'),
                func.count(TrafficFlow.id).label('connection_count'),
            )
            .where(
                and_(
                    TrafficFlow.device_id == device_id,
                    TrafficFlow.timestamp >= since,
                    TrafficFlow.dns_query.is_not(None),
                )
            )
            .group_by(TrafficFlow.dns_query)
            .order_by(desc('connection_count'))
            .limit(limit)
        )
        result = await self.db.execute(query)
        return [dict(row._mapping) for row in result]

    async def get_device_protocol_signals(self, device_id: int, hours: int = 24) -> List[dict]:
        since = datetime.utcnow() - timedelta(hours=hours)
        window_suffix = f"_{hours}h"

        dns_failures_result = await self.db.execute(
            select(func.count())
            .select_from(TrafficFlow)
            .where(
                and_(
                    TrafficFlow.device_id == device_id,
                    TrafficFlow.timestamp >= since,
                    func.json_extract(TrafficFlow.flags, '$.dns_rcode').is_not(None),
                    cast(func.json_extract(TrafficFlow.flags, '$.dns_rcode'), Integer) > 0,
                )
            )
        )
        dns_failures = int(dns_failures_result.scalar() or 0)

        icmp_requests_result = await self.db.execute(
            select(func.count())
            .select_from(TrafficFlow)
            .where(
                and_(
                    TrafficFlow.device_id == device_id,
                    TrafficFlow.timestamp >= since,
                    cast(func.json_extract(TrafficFlow.flags, '$.icmp_type'), Integer) == 8,
                )
            )
        )
        icmp_requests = int(icmp_requests_result.scalar() or 0)

        icmp_destinations_result = await self.db.execute(
            select(func.count(func.distinct(TrafficFlow.dst_ip)))
            .select_from(TrafficFlow)
            .where(
                and_(
                    TrafficFlow.device_id == device_id,
                    TrafficFlow.timestamp >= since,
                    cast(func.json_extract(TrafficFlow.flags, '$.icmp_type'), Integer) == 8,
                )
            )
        )
        icmp_destinations = int(icmp_destinations_result.scalar() or 0)

        top_dns_rcodes_result = await self.db.execute(
            select(
                cast(func.json_extract(TrafficFlow.flags, '$.dns_rcode'), Integer).label('rcode'),
                func.count().label('count'),
            )
            .where(
                and_(
                    TrafficFlow.device_id == device_id,
                    TrafficFlow.timestamp >= since,
                    func.json_extract(TrafficFlow.flags, '$.dns_rcode').is_not(None),
                )
            )
            .group_by('rcode')
            .order_by(desc('count'))
            .limit(3)
        )
        top_dns_rcodes = [f"rcode={row.rcode}:{int(row.count)}" for row in top_dns_rcodes_result]

        return [
            {
                "label": f"dns_failures{window_suffix}",
                "value": dns_failures,
                "note": ", ".join(top_dns_rcodes) or "No DNS failures",
            },
            {
                "label": f"icmp_echo_requests{window_suffix}",
                "value": icmp_requests,
                "note": f"unique_destinations={icmp_destinations}",
            },
        ]


class InferenceHistoryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_device_history(self, device_id: int, days: int = 7, limit: int = 512) -> List[DeviceInferenceHistory]:
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.db.execute(
            select(DeviceInferenceHistory)
            .where(and_(DeviceInferenceHistory.device_id == device_id, DeviceInferenceHistory.timestamp >= since))
            .order_by(DeviceInferenceHistory.timestamp.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def latest_device_history(self, device_id: int) -> Optional[DeviceInferenceHistory]:
        result = await self.db.execute(
            select(DeviceInferenceHistory)
            .where(DeviceInferenceHistory.device_id == device_id)
            .order_by(desc(DeviceInferenceHistory.timestamp))
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def latest_device_history_points(self, device_id: int, limit: int = 2) -> List[DeviceInferenceHistory]:
        result = await self.db.execute(
            select(DeviceInferenceHistory)
            .where(DeviceInferenceHistory.device_id == device_id)
            .order_by(desc(DeviceInferenceHistory.timestamp))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_behavior_baseline(self, device_id: int, days: int = 7) -> List[dict]:
        history = await self.get_device_history(device_id, days=days, limit=1024)
        if not history:
            return []

        metrics = ["total_bytes", "unique_destinations", "unique_ports", "dns_queries", "packet_rate"]
        rows = []
        latest = history[-1].features or {}
        for metric in metrics:
            values = []
            for item in history:
                features = item.features or {}
                value = features.get(metric)
                if value is not None:
                    values.append(float(value))
            if not values:
                continue
            values.sort()
            p95_index = min(len(values) - 1, max(0, int(round((len(values) - 1) * 0.95))))
            median_index = len(values) // 2
            rows.append(
                {
                    "metric": metric,
                    "median": float(values[median_index]),
                    "p95": float(values[p95_index]),
                    "latest": float(latest.get(metric)) if latest.get(metric) is not None else None,
                }
            )
        return rows


class BehaviorAlertService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _normalize_alert(self, alert: DeviceBehaviorAlert) -> DeviceBehaviorAlert:
        alert.resolved = bool(alert.resolved)
        if isinstance(alert.evidence, str):
            try:
                alert.evidence = json.loads(alert.evidence)
            except json.JSONDecodeError:
                alert.evidence = {"raw": alert.evidence}
        return alert

    def _alert_category(self, alert_type: str) -> str:
        return "protocol" if alert_type in PROTOCOL_ALERT_TYPES else "behavior"

    def _alert_weight(self, alert_type: str) -> float:
        if alert_type in {"beaconing_suspected", "destination_novelty"}:
            return 0.2
        if alert_type in PROTOCOL_ALERT_TYPES:
            return 0.16
        return 0.18

    def _decay_multiplier(self, timestamp: datetime, now: datetime) -> float:
        age = max(0.0, (now - timestamp).total_seconds())
        if age <= 30 * 60:
            return 1.0
        if age <= 2 * 60 * 60:
            return 0.7
        if age <= 6 * 60 * 60:
            return 0.4
        return 0.15

    def _severity_from_score(self, score: float) -> str:
        if score >= 18.0:
            return "critical"
        if score >= 8.0:
            return "warning"
        return "info"

    async def list_device_alerts(
        self,
        device_id: int,
        limit: int = 20,
        since_hours: int = 168,
    ) -> tuple[List[DeviceBehaviorAlert], int]:
        since = datetime.utcnow() - timedelta(hours=since_hours)
        count_result = await self.db.execute(
            select(func.count()).select_from(DeviceBehaviorAlert).where(
                and_(DeviceBehaviorAlert.device_id == device_id, DeviceBehaviorAlert.timestamp >= since)
            )
        )
        total = int(count_result.scalar() or 0)
        result = await self.db.execute(
            select(DeviceBehaviorAlert)
            .where(and_(DeviceBehaviorAlert.device_id == device_id, DeviceBehaviorAlert.timestamp >= since))
            .order_by(desc(DeviceBehaviorAlert.timestamp))
            .limit(limit)
        )
        return [self._normalize_alert(alert) for alert in result.scalars().all()], total

    async def latest_risk_contributors(self, device_id: int, lookback_hours: int = 24) -> List[dict]:
        since = datetime.utcnow() - timedelta(hours=lookback_hours)
        result = await self.db.execute(
            select(DeviceBehaviorAlert)
            .where(and_(DeviceBehaviorAlert.device_id == device_id, DeviceBehaviorAlert.timestamp >= since))
            .order_by(desc(DeviceBehaviorAlert.timestamp))
            .limit(64)
        )
        alerts = list(result.scalars().all())
        now = datetime.utcnow()
        contributors_by_type = {}
        for alert in alerts:
            self._normalize_alert(alert)
            category = self._alert_category(alert.alert_type)
            weight = self._alert_weight(alert.alert_type)
            raw_score = float(alert.score or 0.0)
            effective_score = round(raw_score * weight * self._decay_multiplier(alert.timestamp, now), 4)
            if effective_score <= 0:
                continue
            existing = contributors_by_type.get(alert.alert_type)
            if existing and existing["effective_score"] >= effective_score:
                continue
            contributors_by_type[alert.alert_type] = {
                "contributor": alert.alert_type,
                "category": category,
                "severity": self._severity_from_score(effective_score),
                "score": raw_score,
                "raw_score": raw_score,
                "effective_score": effective_score,
                "weight": weight,
                "details": alert.description or alert.title,
                "reason": alert.title,
                "last_seen": alert.timestamp,
            }
        return sorted(contributors_by_type.values(), key=lambda item: item["effective_score"], reverse=True)


class AlertService:
    """Unified feed combining anomalies + behavior alerts across all devices."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_unified_alerts(
        self,
        limit: int = 50,
        severity: Optional[str] = None,
        since_hours: int = 24,
    ) -> tuple[list, int]:
        since_param = f"-{since_hours} hours"
        sev_clause = "AND severity = :severity" if severity else ""

        sql_text = f"""
            SELECT
                'isolation_forest' AS source,
                a.id,
                a.device_id,
                d.hostname  AS device_hostname,
                d.ip_address AS device_ip,
                a.anomaly_type AS alert_type,
                a.description  AS title,
                a.severity,
                a.score,
                a.timestamp,
                a.resolved
            FROM anomalies a
            LEFT JOIN devices d ON d.id = a.device_id
            WHERE a.timestamp >= datetime('now', :since)
            {sev_clause}

            UNION ALL

            SELECT
                'behavior' AS source,
                b.id,
                b.device_id,
                d.hostname  AS device_hostname,
                d.ip_address AS device_ip,
                b.alert_type,
                b.title,
                b.severity,
                b.score,
                b.timestamp,
                b.resolved
            FROM device_behavior_alerts b
            LEFT JOIN devices d ON d.id = b.device_id
            WHERE b.timestamp >= datetime('now', :since)
            {sev_clause}

            ORDER BY timestamp DESC
            LIMIT :limit
        """

        count_sql = f"""
            SELECT COUNT(*) FROM (
                SELECT a.id FROM anomalies a
                WHERE a.timestamp >= datetime('now', :since)
                {sev_clause}
                UNION ALL
                SELECT b.id FROM device_behavior_alerts b
                WHERE b.timestamp >= datetime('now', :since)
                {sev_clause}
            )
        """

        params: dict = {"since": since_param, "limit": limit}
        if severity:
            params["severity"] = severity

        rows = (await self.db.execute(text(sql_text), params)).fetchall()
        total = int((await self.db.execute(text(count_sql), params)).scalar() or 0)

        items = []
        for row in rows:
            items.append(
                {
                    "source": row.source,
                    "id": row.id,
                    "device_id": row.device_id,
                    "device_hostname": row.device_hostname,
                    "device_ip": row.device_ip,
                    "alert_type": row.alert_type,
                    "title": row.title,
                    "severity": row.severity,
                    "score": float(row.score or 0.0),
                    "timestamp": row.timestamp,
                    "resolved": bool(row.resolved),
                }
            )
        return items, total
