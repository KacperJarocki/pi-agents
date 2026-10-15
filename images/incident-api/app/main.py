"""Incident API: snapshot, changes after a cursor, and a live WebSocket feed.

The incident projector writes `incidents`, `incident_feed` and `gateway_health`
and NOTIFYs `incident_feed` (cursor) and `gateway_health` (gateway ID). The
API treats notifications only as a wake-up: it always reads feed rows with
`cursor > last sent`, so a client sees every change once and in order. This
relies on a single projector writing the feed (cursors commit in order).

Environment: DATABASE_URL (required), STATIC_DIR (optional live page).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import pathlib
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

FEED_CHANNEL = "incident_feed"
HEALTH_CHANNEL = "gateway_health"
PING_INTERVAL_S = 20
BATCH = 500

STATIC_DIR = pathlib.Path(os.environ.get("STATIC_DIR", pathlib.Path(__file__).parent / "static"))


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

    async def snapshot(self, active_only: bool, limit: int = 500) -> dict[str, Any]:
        async with self.pool.connection() as conn:
            async with conn.transaction():
                # One consistent view: incidents as of the returned cursor.
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cur = await conn.execute("SELECT coalesce(max(cursor), 0) AS c FROM incident_feed")
                cursor = (await cur.fetchone())["c"]
                cur = await conn.execute(
                    "SELECT last_transition, committed_at FROM incidents"
                    + (" WHERE active" if active_only else "")
                    + " ORDER BY detected_at DESC LIMIT %s",
                    (limit,),
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


def create_app(dsn: str | None = None) -> FastAPI:
    store = Store(dsn or os.environ["DATABASE_URL"])

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
    async def incidents(active: bool = True, limit: int = Query(500, ge=1, le=5000)):
        return await store.snapshot(active, limit)

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
            return RedirectResponse("/live/")

    return app


def main() -> None:  # pragma: no cover - wiring only
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), proxy_headers=True)


if __name__ == "__main__":  # pragma: no cover
    main()
