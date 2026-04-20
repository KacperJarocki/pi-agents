from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from contextlib import asynccontextmanager
from .config import get_settings

settings = get_settings()

DATABASE_URL = f"sqlite+aiosqlite:///{settings.database_path}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    connect_args={
        "timeout": 5.0,
    },
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


async def init_db():
    async with engine.begin() as conn:
        # Enable WAL mode for better concurrent read/write performance
        await conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        await conn.exec_driver_sql("PRAGMA busy_timeout=5000")
        
        await conn.run_sync(Base.metadata.create_all)

        # Lightweight SQLite schema alignment for existing DBs.
        # SQLite has no IF NOT EXISTS for ADD COLUMN, so we introspect first.
        result = await conn.exec_driver_sql("PRAGMA table_info(traffic_flows)")
        cols = {row[1] for row in result}
        if "dns_query" not in cols:
            await conn.exec_driver_sql(
                "ALTER TABLE traffic_flows ADD COLUMN dns_query TEXT"
            )

        result = await conn.exec_driver_sql("PRAGMA table_info(devices)")
        cols = {row[1] for row in result}
        if "last_inference_score" not in cols:
            await conn.exec_driver_sql(
                "ALTER TABLE devices ADD COLUMN last_inference_score REAL"
            )
        if "last_inference_at" not in cols:
            await conn.exec_driver_sql(
                "ALTER TABLE devices ADD COLUMN last_inference_at TIMESTAMP"
            )

        await conn.exec_driver_sql(
            """
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
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_behavior_alert_device_time ON device_behavior_alerts(device_id, timestamp)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_behavior_alert_device_type_bucket ON device_behavior_alerts(device_id, alert_type, bucket_start)"
        )
        # Retention DELETE filters only on timestamp — standalone index avoids full-table scan
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_behavior_alerts_timestamp ON device_behavior_alerts(timestamp)"
        )
        # Speeds up list_anomalies(resolved=False) and auto-resolve UPDATE
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_anomalies_resolved_time ON anomalies(resolved, timestamp)"
        )
        # Speeds up retention DELETE on device_inference_history (no device_id filter)
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS idx_inference_history_timestamp ON device_inference_history(timestamp)"
        )


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
