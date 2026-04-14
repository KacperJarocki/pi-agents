import aiosqlite
import structlog
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
                dns_query TEXT
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
        
        await self.conn.commit()
        log.info("database_initialized", path=self.db_path)
    
    async def close(self):
        if self.conn:
            await self.conn.close()
    
    async def get_or_create_device(self, ip_address: str, mac_address: str = None) -> int:
        if mac_address:
            row = await self.conn.executerow(
                "SELECT id FROM devices WHERE mac_address = ?",
                (mac_address,)
            )
            if row:
                await self.conn.execute(
                    "UPDATE devices SET last_seen = CURRENT_TIMESTAMP, ip_address = ? WHERE id = ?",
                    (ip_address, row['id'])
                )
                return row['id']
        
        row = await self.conn.executerow(
            "SELECT id FROM devices WHERE ip_address = ?",
            (ip_address,)
        )
        
        if row:
            await self.conn.execute(
                "UPDATE devices SET last_seen = CURRENT_TIMESTAMP WHERE id = ?",
                (row['id'],)
            )
            return row['id']
        
        cursor = await self.conn.execute(
            "INSERT INTO devices (mac_address, ip_address) VALUES (?, ?)",
            (mac_address, ip_address)
        )
        await self.conn.commit()
        return cursor.lastrowid
    
    async def insert_flows(self, flows: List[Dict]) -> List[int]:
        ids = []
        for flow in flows:
            device_id = await self.get_or_create_device(
                flow["src_ip"],
                flow.get("mac_address")
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
                str({
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
