import aiosqlite
import structlog
import json
from datetime import datetime
from typing import List, Dict, Optional

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: Optional[aiosqlite.Connection] = None
    
    async def init(self):
        self.conn = await aiosqlite.connect(self.db_path)
        self.conn.row_factory = aiosqlite.Row
        
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mac_address TEXT UNIQUE NOT NULL,
                ip_address TEXT NOT NULL,
                hostname TEXT,
                device_type TEXT,
                first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                risk_score REAL DEFAULT 0.0,
                last_inference_score REAL,
                last_inference_at TIMESTAMP,
                extra_data TEXT
            );
            
            CREATE INDEX IF NOT EXISTS idx_devices_mac ON devices(mac_address);
            CREATE INDEX IF NOT EXISTS idx_devices_ip ON devices(ip_address);
            
            CREATE TABLE IF NOT EXISTS traffic_flows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                src_ip TEXT NOT NULL,
                dst_ip TEXT NOT NULL,
                src_port INTEGER,
                dst_port INTEGER,
                protocol TEXT NOT NULL,
                bytes_sent INTEGER DEFAULT 0,
                bytes_received INTEGER DEFAULT 0,
                packets INTEGER DEFAULT 1,
                duration_ms INTEGER DEFAULT 0,
                dns_query TEXT,
                flags TEXT
            );
            
            CREATE INDEX IF NOT EXISTS idx_flows_device_time ON traffic_flows(device_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_flows_dst_ip ON traffic_flows(dst_ip);
            
            CREATE TABLE IF NOT EXISTS anomalies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                anomaly_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                score REAL NOT NULL,
                description TEXT,
                flow_ids TEXT,
                features TEXT,
                resolved INTEGER DEFAULT 0,
                resolved_at TIMESTAMP
            );
            
            CREATE INDEX IF NOT EXISTS idx_anomalies_device ON anomalies(device_id);
            CREATE INDEX IF NOT EXISTS idx_anomalies_timestamp ON anomalies(timestamp);

            CREATE TABLE IF NOT EXISTS device_inference_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                bucket_start TIMESTAMP,
                anomaly_score REAL NOT NULL,
                risk_score REAL NOT NULL,
                is_anomaly INTEGER DEFAULT 0,
                severity TEXT NOT NULL,
                features TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_inference_history_device_time ON device_inference_history(device_id, timestamp);

            CREATE TABLE IF NOT EXISTS device_behavior_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_id INTEGER NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                bucket_start TIMESTAMP,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                score REAL NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                evidence TEXT,
                resolved INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_behavior_alert_device_time ON device_behavior_alerts(device_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_behavior_alert_device_type_bucket ON device_behavior_alerts(device_id, alert_type, bucket_start);
            
            CREATE TABLE IF NOT EXISTS model_metadata (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_type TEXT NOT NULL,
                version TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                training_samples INTEGER DEFAULT 0,
                accuracy REAL,
                features_used TEXT,
                parameters TEXT,
                is_active INTEGER DEFAULT 0
            );
        """)

        # Best-effort schema alignment for existing DBs.
        await self._ensure_column("traffic_flows", "dns_query", "TEXT")
        await self._ensure_column("traffic_flows", "flags", "TEXT")
        await self._ensure_column("devices", "last_inference_score", "REAL")
        await self._ensure_column("devices", "last_inference_at", "TIMESTAMP")

        await self.conn.commit()
        log.info("database_initialized", path=self.db_path)

    async def _ensure_column(self, table: str, column: str, column_type: str):
        cursor = await self.conn.execute(f"PRAGMA table_info({table})")
        cols = {row[1] for row in await cursor.fetchall()}
        if column not in cols:
            await self.conn.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"
            )
    
    async def close(self):
        if self.conn:
            await self.conn.close()

    async def _fetch_one(self, query: str, params: tuple):
        cursor = await self.conn.execute(query, params)
        return await cursor.fetchone()
    
    async def get_or_create_device(self, ip_address: str, mac_address: str = None, hostname: str = None) -> int:
        # Keep DB invariant: mac_address is NOT NULL.
        mac_address = mac_address or f"ip:{ip_address}"

        if mac_address and not mac_address.startswith("ip:"):
            row = await self._fetch_one(
                "SELECT id, mac_address FROM devices WHERE mac_address = ?",
                (mac_address,)
            )
            if row:
                await self.conn.execute(
                    "UPDATE devices SET last_seen = CURRENT_TIMESTAMP, ip_address = ?, hostname = COALESCE(?, hostname) WHERE id = ?",
                    (ip_address, hostname, row['id'])
                )
                log.info("device_updated_from_mac", device_id=row['id'], mac=mac_address, ip=ip_address)
                return row['id']
        
        row = await self._fetch_one(
            "SELECT id FROM devices WHERE ip_address = ?",
            (ip_address,)
        )
        
        if row:
            # If we previously inserted a placeholder MAC, try to upgrade it.
            await self.conn.execute(
                "UPDATE devices SET last_seen = CURRENT_TIMESTAMP, hostname = COALESCE(?, hostname), mac_address = CASE WHEN mac_address LIKE 'ip:%' THEN ? ELSE mac_address END WHERE id = ?",
                (hostname, mac_address, row['id'])
            )
            log.info("device_updated_from_ip", device_id=row['id'], mac=mac_address, ip=ip_address)
            return row['id']
        
        cursor = await self.conn.execute(
            "INSERT INTO devices (mac_address, ip_address, hostname) VALUES (?, ?, ?)",
            (mac_address, ip_address, hostname)
        )
        await self.conn.commit()
        log.info("device_created", device_id=cursor.lastrowid, mac=mac_address, ip=ip_address, hostname=hostname)
        return cursor.lastrowid
    
    async def insert_flows(self, flows: List[Dict]) -> List[int]:
        ids = []
        for flow in flows:
            device_id = await self.get_or_create_device(
                flow["device_ip"],
                flow.get("device_mac"),
                flow.get("hostname"),
            )
            
            cursor = await self.conn.execute("""
                INSERT INTO traffic_flows 
                (device_id, src_ip, dst_ip, src_port, dst_port, protocol, bytes_sent, dns_query)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                device_id,
                flow["src_ip"],
                flow["dst_ip"],
                flow.get("src_port", 0),
                flow.get("dst_port", 0),
                flow["protocol"],
                flow.get("bytes", 0),
                flow.get("dns_query")
            ))
            ids.append(cursor.lastrowid)
        
        await self.conn.commit()
        return ids
    
    async def update_device_stats(self, aggregated: Dict[str, Dict]):
        for ip, stats in aggregated.items():
            await self.conn.execute("""
                UPDATE devices 
                SET last_seen = CURRENT_TIMESTAMP,
                    extra_data = ?
                WHERE ip_address = ?
            """, (
                json.dumps({
                    "total_bytes": stats["total_bytes"],
                    "packet_count": stats["packet_count"],
                    "unique_connections": len(stats["connections"]),
                    "unique_destinations": len(stats["dst_ips"]),
                    "ports": list(stats["dst_ports"])[:10]
                }),
                ip
            ))
        await self.conn.commit()
    
    async def get_recent_flows(self, hours: int = 24, limit: int = 1000) -> List[Dict]:
        cursor = await self.conn.execute("""
            SELECT * FROM traffic_flows 
            WHERE timestamp >= datetime('now', '-' || ? || ' hours')
            ORDER BY timestamp DESC
            LIMIT ?
        """, (hours, limit))
        
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
    
    async def get_device_flows(self, device_id: int, limit: int = 100) -> List[Dict]:
        cursor = await self.conn.execute("""
            SELECT * FROM traffic_flows 
            WHERE device_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
        """, (device_id, limit))
        
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
