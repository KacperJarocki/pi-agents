from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from contextlib import asynccontextmanager
from .config import get_settings

settings = get_settings()

DATABASE_URL = f"sqlite+aiosqlite:///{settings.database_path}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


async def init_db():
    async with engine.begin() as conn:
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
