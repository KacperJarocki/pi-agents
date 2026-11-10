"""Incident API: snapshot, changes after a cursor, and a live WebSocket feed.

The incident projector writes `incidents`, `incident_feed` and `gateway_health`
and NOTIFYs `incident_feed` (cursor) and `gateway_health` (gateway ID). The
API treats notifications only as a wake-up: it always reads feed rows with
`cursor > last sent`, so a client sees every change once and in order. This
relies on a single projector writing the feed (cursors commit in order).

The device overview (`/api/v2/devices`, page at `/`) joins the latest
rolling-window view per device (`device_features`, from traffic.features.v2)
with incident counts and, when GATEWAY_AGENT_URL is set, the DHCP leases the
gateway-agent reports (hostname, IP). Devices are matched by MAC: a device key
is `<gateway_id>:<mac>`.

Environment: DATABASE_URL (required), STATIC_DIR (optional pages),
GATEWAY_AGENT_URL (optional, e.g. http://gateway-agent:7000).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import pathlib
import time
from typing import Any

import httpx
import psycopg
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

FEED_CHANNEL = "incident_feed"
HEALTH_CHANNEL = "gateway_health"
PING_INTERVAL_S = 20
BATCH = 500

STATIC_DIR = pathlib.Path(os.environ.get("STATIC_DIR", pathlib.Path(__file__).parent / "static"))
LEASE_CACHE_S = 10
_SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


def _ms(value: dt.datetime | None) -> int | None:
    return None if value is None else int(value.timestamp() * 1000)


class CursorExpired(Exception):
    """The requested cursor is older than the retained feed."""


class Store:
    """Read side of the projector's tables."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.pool = AsyncConnectionPool(
            dsn, min_size=1, max_size=5, open=False, kwargs={"autocommit": True, "row_factory": dict_row}
        )

    async def snapshot(
        self, active_only: bool, limit: int = 500, device_key: str | None = None
    ) -> dict[str, Any]:
        where = []
        params: list[Any] = []
        if active_only:
            where.append("active")
        if device_key is not None:
            where.append("device_key = %s")
            params.append(device_key)
        async with self.pool.connection() as conn:
            async with conn.transaction():
                # One consistent view: incidents as of the returned cursor.
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cur = await conn.execute("SELECT coalesce(max(cursor), 0) AS c FROM incident_feed")
                cursor = (await cur.fetchone())["c"]
                cur = await conn.execute(
                    "SELECT last_transition, committed_at FROM incidents"
                    + (" WHERE " + " AND ".join(where) if where else "")
                    + " ORDER BY detected_at DESC LIMIT %s",
                    (*params, limit),
                )
                rows = await cur.fetchall()
        incidents = [{**r["last_transition"], "committed_at_ms": _ms(r["committed_at"])} for r in rows]
        return {"cursor": cursor, "incidents": incidents}

    async def changes(self, after: int, limit: int = BATCH) -> dict[str, Any]:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT min(cursor) AS m FROM incident_feed")
            oldest = (await cur.fetchone())["m"]
            if oldest is not None and after < oldest - 1:
                raise CursorExpired()
            cur = await conn.execute(
                "SELECT cursor, incident_id, revision, payload, committed_at FROM incident_feed"
                " WHERE cursor > %s ORDER BY cursor LIMIT %s",
                (after, limit),
            )
            rows = await cur.fetchall()
        changes = [
            {
                "cursor": r["cursor"],
                "incident_id": r["incident_id"],
                "revision": r["revision"],
                "transition": r["payload"],
                "committed_at_ms": _ms(r["committed_at"]),
            }
            for r in rows
        ]
        return {"changes": changes, "cursor": changes[-1]["cursor"] if changes else after}

    async def transitions(self, incident_id: str) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT payload, received_at FROM incident_transitions"
                " WHERE incident_id = %s ORDER BY revision",
                (incident_id,),
            )
            rows = await cur.fetchall()
        return [{**r["payload"], "committed_at_ms": _ms(r["received_at"])} for r in rows]

    async def device_rows(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Latest views per device/horizon and incident counts per device."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT device_key, gateway_id, horizon_seconds, window_end, features, received_at"
                " FROM device_features ORDER BY device_key, horizon_seconds"
            )
            features = await cur.fetchall()
            cur = await conn.execute(
                """
                SELECT device_key, gateway_id,
                       count(*) AS total,
                       count(*) FILTER (WHERE active) AS active,
                       max(detected_at) AS last_detected,
                       array_agg(severity) FILTER (WHERE active) AS active_severities
                FROM incidents GROUP BY device_key, gateway_id
                """
            )
            incidents = await cur.fetchall()
        return features, incidents

    async def health(self) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT gateway_id, component, kind, count, first_sequence, last_sequence,"
                " first_event_time, last_event_time, detail, received_at"
                " FROM gateway_health ORDER BY gateway_id, component, kind"
            )
            rows = await cur.fetchall()
        return [
            {
                **{k: r[k] for k in ("gateway_id", "component", "kind", "count", "first_sequence", "last_sequence", "detail")},
                "first_event_time_ms": _ms(r["first_event_time"]),
                "last_event_time_ms": _ms(r["last_event_time"]),
                "received_at_ms": _ms(r["received_at"]),
            }
            for r in rows
        ]


class Leases:
    """DHCP leases from the gateway-agent's /status, cached for a few seconds.

    Optional: without GATEWAY_AGENT_URL (or when the agent is unreachable)
    devices are listed without hostname and IP.
    """

    def __init__(self, base_url: str | None, fetch=None):
        self.base_url = (base_url or "").rstrip("/")
        self.fetch = fetch or self._fetch
        self.cached: list[dict[str, Any]] = []
        self.fetched_at = 0.0
        self.error: str | None = None if self.base_url else "GATEWAY_AGENT_URL not set"

    async def _fetch(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{self.base_url}/status")
            resp.raise_for_status()
            return resp.json().get("connected_clients") or []

    async def get(self) -> list[dict[str, Any]]:
        if not self.base_url:
            return []
        if time.monotonic() - self.fetched_at < LEASE_CACHE_S:
            return self.cached
        try:
            self.cached = await self.fetch()
            self.error = None
        except Exception as err:  # keep serving the last known leases
            self.error = f"gateway-agent unavailable: {err.__class__.__name__}"
        self.fetched_at = time.monotonic()
        return self.cached


def _mac(device_key: str) -> str:
    """`<gateway_id>:<mac>` -> mac (the gateway ID has no colon)."""
    return device_key.split(":", 1)[1].lower() if ":" in device_key else device_key.lower()


def build_devices(features, incidents, leases) -> list[dict[str, Any]]:
    devices: dict[str, dict[str, Any]] = {}

    def entry(mac: str) -> dict[str, Any]:
        return devices.setdefault(
            mac,
            {
                "mac": mac,
                "device_key": None,
                "gateway_id": None,
                "hostname": None,
                "ip": None,
                "lease_expires_at_ms": None,
                "in_lease_table": False,
                "last_seen_ms": None,
                "features": {},
                "incidents": {"active": 0, "total": 0, "last_detected_ms": None, "severity": None},
            },
        )

    for f in features:
        d = entry(_mac(f["device_key"]))
        d["device_key"], d["gateway_id"] = f["device_key"], f["gateway_id"]
        window_end = _ms(f["window_end"])
        d["features"][str(f["horizon_seconds"])] = {**f["features"], "window_end_ms": window_end}
        d["last_seen_ms"] = max(d["last_seen_ms"] or 0, window_end or 0) or None
    for i in incidents:
        d = entry(_mac(i["device_key"]))
        d["device_key"] = d["device_key"] or i["device_key"]
        d["gateway_id"] = d["gateway_id"] or i["gateway_id"]
        severities = [s for s in (i["active_severities"] or []) if s]
        d["incidents"] = {
            "active": i["active"],
            "total": i["total"],
            "last_detected_ms": _ms(i["last_detected"]),
            "severity": max(severities, key=lambda s: _SEVERITY_RANK.get(s, 0)) if severities else None,
        }
    for lease in leases:
        mac = str(lease.get("mac_address") or "").lower()
        if not mac:
            continue
        d = entry(mac)
        d["hostname"] = lease.get("hostname") or None
        d["ip"] = lease.get("ip_address") or None
        expires = lease.get("lease_expires_at")
        d["lease_expires_at_ms"] = int(expires) * 1000 if str(expires or "").isdigit() else None
        d["in_lease_table"] = True
    return sorted(
        devices.values(),
        key=lambda d: (-(d["incidents"]["active"]), -(d["last_seen_ms"] or 0), d["hostname"] or d["mac"]),
    )


def create_app(dsn: str | None = None, leases: Leases | None = None) -> FastAPI:
    store = Store(dsn or os.environ["DATABASE_URL"])
    leases = leases or Leases(os.environ.get("GATEWAY_AGENT_URL"))

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        await store.pool.open()
        yield
        await store.pool.close()

    app = FastAPI(title="pi-agents incident API", lifespan=lifespan)
    app.state.store = store

    @app.get("/healthz")
    async def healthz():
        async with store.pool.connection() as conn:
            await conn.execute("SELECT 1")
        return {"ok": True}

    @app.get("/api/v2/incidents")
    async def incidents(
        active: bool = True, limit: int = Query(500, ge=1, le=5000), device: str | None = None
    ):
        return await store.snapshot(active, limit, device_key=device)

    @app.get("/api/v2/incidents/{incident_id}/transitions")
    async def transitions(incident_id: str):
        rows = await store.transitions(incident_id)
        if not rows:
            raise HTTPException(status_code=404, detail="unknown incident")
        return {"incident_id": incident_id, "transitions": rows}

    @app.get("/api/v2/devices")
    async def devices():
        features, incidents = await store.device_rows()
        lease_rows = await leases.get()
        return {
            "devices": build_devices(features, incidents, lease_rows),
            "leases": {"available": leases.error is None, "error": leases.error},
            "generated_at_ms": int(time.time() * 1000),
        }

    @app.get("/api/v2/incidents/changes")
    async def changes(after: int = Query(..., ge=0), limit: int = Query(BATCH, ge=1, le=5000)):
        try:
            return await store.changes(after, limit)
        except CursorExpired:
            return JSONResponse(
                status_code=410,
                content={"detail": "cursor expired; reload /api/v2/incidents", "snapshot": "/api/v2/incidents"},
            )

    @app.get("/api/v2/health")
    async def health():
        return {"gateways": await store.health()}

    @app.websocket("/ws/v2/incidents")
    async def live(ws: WebSocket, after: int | None = None):
        await ws.accept()
        # LISTEN before reading anything, so no change slips between catch-up and live.
        async with await psycopg.AsyncConnection.connect(store.dsn, autocommit=True) as listener:
            await listener.execute(f"LISTEN {FEED_CHANNEL}")
            await listener.execute(f"LISTEN {HEALTH_CHANNEL}")
            await ws.send_json({"type": "health", "gateways": await store.health()})

            async def send_snapshot() -> int:
                snap = await store.snapshot(active_only=False)
                await ws.send_json({"type": "snapshot", **snap})
                return snap["cursor"]

            async def send_changes(last: int) -> int:
                while True:
                    try:
                        batch = await store.changes(last)
                    except CursorExpired:
                        await ws.send_json({"type": "reset", "reason": "cursor expired"})
                        return await send_snapshot()
                    for change in batch["changes"]:
                        await ws.send_json({"type": "change", **change})
                    last = batch["cursor"]
                    if len(batch["changes"]) < BATCH:
                        return last

            last = await send_snapshot() if after is None else await send_changes(after)
            await ws.send_json({"type": "live", "cursor": last})

            try:
                while True:
                    woke = False
                    async for note in listener.notifies(timeout=PING_INTERVAL_S, stop_after=1):
                        woke = True
                        if note.channel == FEED_CHANNEL:
                            last = await send_changes(last)
                        else:
                            await ws.send_json({"type": "health", "gateways": await store.health()})
                    if not woke:
                        # Keeps proxies from closing an idle socket.
                        await ws.send_json({"type": "ping", "cursor": last})
            except (WebSocketDisconnect, RuntimeError):
                return

    if STATIC_DIR.is_dir() and any(STATIC_DIR.iterdir()):
        app.mount("/live", StaticFiles(directory=STATIC_DIR, html=True), name="live")

        @app.get("/", include_in_schema=False)
        async def root():
            return FileResponse(STATIC_DIR / "devices.html", media_type="text/html")

    return app


def main() -> None:  # pragma: no cover - wiring only
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), proxy_headers=True)


if __name__ == "__main__":  # pragma: no cover
    main()
