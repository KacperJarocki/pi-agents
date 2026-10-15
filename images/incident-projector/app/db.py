"""PostgreSQL side of the incident projector: migrations and the per-transition transaction."""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import psycopg

FEED_CHANNEL = "incident_feed"
FEED_RETENTION = dt.timedelta(days=7)

_STATUS = {1: "OPEN", 2: "UPDATE", 3: "REOPEN", 4: "RESOLVE"}
_SEVERITY = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}
_FAMILY = {1: "PORT_SCAN"}


def migrate(conn: psycopg.Connection) -> list[str]:
    """Applies migrations from app/migrations in name order; returns the new ones."""
    applied: list[str] = []
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        # One migrator at a time (several projector replicas may start together).
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('pi-agents-migrations'))")
        done = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        files = sorted((pathlib.Path(__file__).parent / "migrations").glob("*.sql"))
        for f in files:
            if f.name in done:
                continue
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migrations (version) VALUES (%s)", (f.name,))
            applied.append(f.name)
    return applied


def _ts(millis: int) -> dt.datetime | None:
    if not millis:
        return None
    return dt.datetime.fromtimestamp(millis / 1000, tz=dt.timezone.utc)


def transition_payload(t) -> dict:
    """The JSON shape stored in incidents.last_transition and the feed."""
    return {
        "transition_id": t.transition_id,
        "incident_id": t.incident_id,
        "revision": t.revision,
        "status": _STATUS.get(t.status, "UNSPECIFIED"),
        "threat_family": _FAMILY.get(t.threat_family, "UNSPECIFIED"),
        "rule_id": t.rule_id,
        "rule_version": t.rule_version,
        "severity": _SEVERITY.get(t.severity, "UNSPECIFIED"),
        "device_key": t.envelope.device_key,
        "gateway_id": t.envelope.gateway_id,
        "horizons": [
            {
                "horizon_seconds": h.horizon_seconds,
                "value": h.value,
                "open_threshold": h.open_threshold,
                "over_threshold": h.over_threshold,
            }
            for h in t.horizons
        ],
        "episode_start_ms": t.episode_start_unix_millis,
        "evidence_event_time_ms": t.evidence_event_time_unix_millis,
        "evidence_ingest_time_ms": t.evidence_ingest_time_unix_millis,
        "detected_at_ms": t.detected_at_unix_millis,
        "sample_event_ids": list(t.sample_event_ids),
        "feature_version": t.feature_version,
    }


def apply_transition(conn: psycopg.Connection, t) -> int | None:
    """Applies one DetectionTransition in a single transaction.

    Returns the feed cursor, or None when the transition was already applied
    (redelivery or replay). A transition older than the stored revision is
    recorded and fed to browsers (which apply revisions in order) but does not
    replace the incident's current state.
    """
    payload = transition_payload(t)
    status = payload["status"]
    with conn.transaction():
        inserted = conn.execute(
            "INSERT INTO incident_transitions (transition_id, incident_id, revision, status, payload)"
            " VALUES (%s, %s, %s, %s, %s) ON CONFLICT (transition_id) DO NOTHING"
            " RETURNING transition_id",
            (t.transition_id, t.incident_id, t.revision, status, json.dumps(payload)),
        ).fetchone()
        if inserted is None:
            return None
        conn.execute(
            """
            INSERT INTO incidents (
                incident_id, device_key, gateway_id, threat_family, rule_id, rule_version,
                status, active, severity, revision, episode_start, evidence_event_time,
                evidence_ingest_time, detected_at, last_transition)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (incident_id) DO UPDATE SET
                status = EXCLUDED.status,
                active = EXCLUDED.active,
                severity = EXCLUDED.severity,
                revision = EXCLUDED.revision,
                episode_start = EXCLUDED.episode_start,
                evidence_event_time = EXCLUDED.evidence_event_time,
                evidence_ingest_time = EXCLUDED.evidence_ingest_time,
                detected_at = EXCLUDED.detected_at,
                committed_at = now(),
                last_transition = EXCLUDED.last_transition
            WHERE incidents.revision < EXCLUDED.revision
            """,
            (
                t.incident_id,
                payload["device_key"],
                payload["gateway_id"],
                payload["threat_family"],
                t.rule_id,
                t.rule_version,
                status,
                status != "RESOLVE",
                payload["severity"],
                t.revision,
                _ts(t.episode_start_unix_millis),
                _ts(t.evidence_event_time_unix_millis),
                _ts(t.evidence_ingest_time_unix_millis),
                _ts(t.detected_at_unix_millis),
                json.dumps(payload),
            ),
        )
        cursor = conn.execute(
            "INSERT INTO incident_feed (incident_id, revision, payload) VALUES (%s, %s, %s)"
            " RETURNING cursor",
            (t.incident_id, t.revision, json.dumps(payload)),
        ).fetchone()[0]
        conn.execute("SELECT pg_notify(%s, %s)", (FEED_CHANNEL, str(cursor)))
    return cursor


def prune_feed(conn: psycopg.Connection, older_than: dt.timedelta = FEED_RETENTION) -> int:
    """Deletes feed rows older than the retention; returns how many."""
    with conn.transaction():
        cur = conn.execute(
            "DELETE FROM incident_feed WHERE committed_at < now() - %s", (older_than,)
        )
        return cur.rowcount
