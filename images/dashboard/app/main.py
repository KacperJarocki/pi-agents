from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
import httpx
import asyncio
import json
from datetime import datetime
from typing import List, Optional
import os

app = FastAPI(title="IoT Security Dashboard")

GATEWAY_API = os.getenv("GATEWAY_API_URL", "http://gateway-api.iot-security:8080")
API_PREFIX = "/api/v1"
REFRESH_INTERVAL = int(os.getenv("REFRESH_INTERVAL", "30"))

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["now"] = datetime.utcnow


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

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


async def fetch_api(endpoint: str):
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(f"{GATEWAY_API}{API_PREFIX}{endpoint}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            return {"error": str(e)}


async def call_api(method: str, endpoint: str, payload: dict | None = None):
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            url = f"{GATEWAY_API}{API_PREFIX}{endpoint}"
            r = await client.request(method, url, json=payload)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            return {"error": str(e)}


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "gateway_api": GATEWAY_API,
            "refresh_interval": REFRESH_INTERVAL,
        },
    )


@app.get("/gateway", response_class=HTMLResponse)
async def gateway_settings(request: Request):
    return await _render_gateway(request)


async def _render_gateway(request: Request, message: str | None = None):
    cfg = await fetch_api("/gateway/wifi/config")
    status = await fetch_api("/gateway/wifi/status")

    return templates.TemplateResponse(
        request,
        "gateway.html",
        {
            "config": (cfg.get("config") if isinstance(cfg, dict) else None) or {},
            "status": status if isinstance(status, dict) else {},
            "message": message,
        },
    )


@app.post("/gateway/validate")
async def gateway_validate(request: Request):
    form = await request.form()
    cfg = {k: form.get(k) for k in form.keys()}
    cfg["channel"] = int(cfg.get("channel") or 6)
    cfg["enabled"] = (cfg.get("enabled") == "on")
    res = await call_api("POST", "/gateway/wifi/validate", cfg)
    return await _render_gateway(request, message=json.dumps(res))


@app.post("/gateway/save")
async def gateway_save(request: Request):
    form = await request.form()
    cfg = {k: form.get(k) for k in form.keys()}
    cfg["channel"] = int(cfg.get("channel") or 6)
    cfg["enabled"] = (cfg.get("enabled") == "on")
    res = await call_api("PUT", "/gateway/wifi/config", cfg)
    msg = "saved" if not res.get("error") else f"error: {res.get('error')}"
    return await _render_gateway(request, message=msg)


@app.post("/gateway/apply")
async def gateway_apply(request: Request):
    form = await request.form()
    cfg = {k: form.get(k) for k in form.keys()}
    cfg["channel"] = int(cfg.get("channel") or 6)
    cfg["enabled"] = (cfg.get("enabled") == "on")
    res = await call_api("POST", "/gateway/wifi/apply", cfg)
    msg = res.get("message") if not res.get("error") else f"error: {res.get('error')}"
    return await _render_gateway(request, message=msg)


@app.post("/gateway/rollback")
async def gateway_rollback(request: Request):
    res = await call_api("POST", "/gateway/wifi/rollback")
    msg = res.get("message") if not res.get("error") else f"error: {res.get('error')}"
    return await _render_gateway(request, message=msg)


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/api/devices")
async def get_devices():
    return await fetch_api("/devices")


@app.get("/api/devices/{device_id}")
async def get_device(device_id: int):
    return await fetch_api(f"/devices/{device_id}")


@app.get("/api/anomalies")
async def get_anomalies(limit: int = 20):
    return await fetch_api(f"/anomalies?limit={limit}")


@app.get("/api/metrics/summary")
async def get_metrics_summary():
    return await fetch_api("/metrics/summary")


@app.get("/api/metrics/timeline")
async def get_timeline(hours: int = 24):
    return await fetch_api(f"/metrics/timeline?hours={hours}")


@app.get("/api/metrics/top-talking")
async def get_top_talkers(limit: int = 10):
    return await fetch_api(f"/metrics/top-talking?limit={limit}")


@app.get("/partial/devices")
async def partial_devices():
    devices_data = await fetch_api("/devices")
    summary_data = await fetch_api("/metrics/summary")
    
    devices = devices_data.get("devices", [])
    
    html = ""
    for device in devices:
        risk = device.get("risk_score", 0)
        status_class = "risk-critical" if risk > 70 else "risk-warning" if risk > 40 else "risk-ok"
        status_icon = "🔴" if risk > 70 else "🟡" if risk > 40 else "🟢"
        connected = bool(device.get("connected"))
        connection_source = device.get("connection_source") or ""
        model_status = device.get("model_status") or "missing"
        connection_badge = (
            f'<span class="inline-block text-xs px-2 py-1 rounded-full bg-green-500/20 text-green-300 border border-green-500/30">Connected via {connection_source}</span>'
            if connected else
            '<span class="inline-block text-xs px-2 py-1 rounded-full bg-white/10 text-gray-300 border border-white/10">Not connected</span>'
        )
        model_badge = (
            '<span class="inline-block text-xs px-2 py-1 rounded-full bg-blue-500/20 text-blue-300 border border-blue-500/30">Model ready</span>'
            if model_status == "ready" else
            '<span class="inline-block text-xs px-2 py-1 rounded-full bg-yellow-500/20 text-yellow-300 border border-yellow-500/30">Model missing</span>'
        )
        
        html += f"""
        <div class="device-card {status_class}">
            <div class="device-header">
                <span class="status-icon">{status_icon}</span>
                <span class="device-name">{device.get('hostname', device.get('ip_address', 'Unknown'))}</span>
            </div>
            <div class="mt-2 mb-3 flex flex-wrap gap-2">{connection_badge}{model_badge}</div>
            <div class="device-details">
                <div class="device-info">
                    <span class="label">IP:</span> {device.get('ip_address', 'N/A')}
                </div>
                <div class="device-info">
                    <span class="label">MAC:</span> {device.get('mac_address', 'N/A')}
                </div>
                <div class="device-info">
                    <span class="label">Last seen:</span> {device.get('last_seen', 'N/A')[:19] if device.get('last_seen') else 'N/A'}
                </div>
            </div>
            <div class="risk-bar">
                <div class="risk-fill" style="width: {risk}%"></div>
            </div>
            <div class="risk-score">Risk: {risk:.1f}%</div>
        </div>
        """
    
    if not devices:
        html = '<div class="empty-state">No devices found</div>'
    
    return HTMLResponse(content=html)


@app.get("/partial/anomalies")
async def partial_anomalies(limit: int = 10):
    anomalies_data = await fetch_api(f"/anomalies?limit={limit}")
    
    anomalies = anomalies_data.get("anomalies", [])
    
    html = ""
    for anomaly in anomalies:
        severity = anomaly.get("severity", "unknown")
        severity_icon = "🔴" if severity == "critical" else "🟡"
        
        html += f"""
        <div class="anomaly-card severity-{severity}">
            <div class="anomaly-header">
                {severity_icon}
                <span class="anomaly-type">{anomaly.get('anomaly_type', 'Unknown')}</span>
                <span class="anomaly-time">{anomaly.get('timestamp', 'N/A')[:19] if anomaly.get('timestamp') else 'N/A'}</span>
            </div>
            <div class="anomaly-description">{anomaly.get('description', 'No description')}</div>
            <div class="anomaly-score">Score: {anomaly.get('score', 0):.3f}</div>
        </div>
        """
    
    if not anomalies:
        html = '<div class="empty-state">No anomalies detected</div>'
    
    return HTMLResponse(content=html)


@app.get("/partial/timeline")
async def partial_timeline():
    timeline_data = await fetch_api("/metrics/timeline?hours=24")
    
    data = timeline_data.get("data", [])
    
    if not data:
        return HTMLResponse(content='<div class="empty-state">No timeline data</div>')
    
    max_traffic = max(d.get("total_traffic_mb", 1) for d in data)
    
    html = '<div class="timeline-chart">'
    for point in data:
        traffic = point.get("total_traffic_mb", 0)
        height = (traffic / max_traffic * 100) if max_traffic > 0 else 0
        anomalies = point.get("anomaly_count", 0)
        
        hour = point.get("timestamp", "")[11:16] if point.get("timestamp") else ""
        
        html += f"""
        <div class="timeline-bar">
            <div class="bar-container">
                <div class="bar-fill" style="height: {height}%"></div>
                {f'<div class="anomaly-dot" title="{anomalies} anomalies"></div>' if anomalies > 0 else ''}
            </div>
            <div class="bar-label">{hour}</div>
        </div>
        """
    html += '</div>'
    
    return HTMLResponse(content=html)


@app.get("/partial/top-talkers")
async def partial_top_talkers():
    talkers_data = await fetch_api("/metrics/top-talking?limit=10")
    
    talkers = talkers_data.get("data", [])
    
    if not talkers:
        return HTMLResponse(content='<div class="empty-state">No traffic data</div>')
    
    max_bytes = max(t.get("total_bytes", 1) for t in talkers)
    
    html = '<div class="top-talkers-list">'
    for i, talker in enumerate(talkers, 1):
        bytes_val = talker.get("total_bytes", 0)
        width = (bytes_val / max_bytes * 100) if max_bytes > 0 else 0
        mb = bytes_val / (1024 * 1024)
        
        suspicious = bytes_val > max_bytes * 0.8
        
        html += f"""
        <div class="talker-row {'suspicious' if suspicious else ''}">
            <div class="talker-rank">{i}</div>
            <div class="talker-ip">{talker.get('ip_address', 'Unknown')}</div>
            <div class="talker-bar">
                <div class="talker-fill" style="width: {width}%"></div>
            </div>
            <div class="talker-bytes">{mb:.1f} MB</div>
            {'⚠️' if suspicious else ''}
        </div>
        """
    html += '</div>'
    
    return HTMLResponse(content=html)


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


async def poll_gateway_alerts():
    while True:
        try:
            anomalies_data = await fetch_api("/anomalies?limit=5&resolved=false")
            anomalies = anomalies_data.get("anomalies", [])
            
            if anomalies:
                await manager.broadcast({
                    "type": "new_anomalies",
                    "count": len(anomalies),
                    "data": anomalies
                })
        except Exception:
            pass
        
        await asyncio.sleep(60)


@app.on_event("startup")
async def startup():
    asyncio.create_task(poll_gateway_alerts())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
