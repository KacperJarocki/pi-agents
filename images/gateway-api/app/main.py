from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from contextlib import asynccontextmanager
from sqlalchemy import text
import asyncio
from datetime import datetime
from typing import List
from .core.config import get_settings
from .core.database import init_db
from .core.database import get_db_context
from .core.logging import log
from .routers import devices_router, anomalies_router, metrics_router, gateway_wifi_router, alerts_router

settings = get_settings()

request_count = Counter(
    'gateway_api_requests_total',
    'Total requests',
    ['method', 'endpoint', 'status']
)

request_duration = Histogram(
    'gateway_api_request_duration_seconds',
    'Request duration',
    ['method', 'endpoint']
)

active_devices = Gauge(
    'gateway_api_active_devices',
    'Number of active devices'
)

active_anomalies = Gauge(
    'gateway_api_active_anomalies',
    'Number of unresolved anomalies'
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        log.info("websocket_connected", total_connections=len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        log.info("websocket_disconnected", total_connections=len(self.active_connections))

    async def broadcast(self, message: dict):
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("starting_gateway_api", database=settings.database_path)
    await init_db()
    log.info("database_initialized")
    yield
    log.info("shutting_down_gateway_api")


app = FastAPI(
    title="IoT Security Gateway API",
    description="API for IoT threat detection system",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(devices_router, prefix=settings.api_prefix)
app.include_router(anomalies_router, prefix=settings.api_prefix)
app.include_router(metrics_router, prefix=settings.api_prefix)
app.include_router(gateway_wifi_router, prefix=settings.api_prefix)
app.include_router(alerts_router, prefix=settings.api_prefix)


@app.get("/health")
async def health_check():
    try:
        async with get_db_context() as session:
            await session.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"database_unavailable: {e}")

    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}


@app.get("/metrics")
async def metrics():
    from starlette.responses import Response
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST
    )


@app.websocket("/ws/alerts")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            await websocket.send_json({
                "type": "echo",
                "data": data,
                "timestamp": datetime.utcnow().isoformat()
            })
    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post(f"{settings.api_prefix}/alerts/broadcast")
async def broadcast_alert(alert: dict):
    alert["timestamp"] = datetime.utcnow().isoformat()
    await manager.broadcast(alert)
    return {"status": "broadcast", "recipients": len(manager.active_connections)}


log.info("gateway_api_module_loaded")
