"""Incident projector against a real PostgreSQL.

Set PROJECTOR_TEST_DSN (e.g. postgresql://postgres:test@localhost:55432/postgres)
and install images/incident-projector/requirements*.txt; the tests are
skipped otherwise. app/events_pb2.py is generated on demand.
"""

import datetime as dt
import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECTOR = ROOT / "images" / "incident-projector"
DSN = os.environ.get("PROJECTOR_TEST_DSN")


def _clear_app_modules():
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]


def _load():
    """Imports the projector's `app` package (other images also use `app`)."""
    if not (PROJECTOR / "app" / "events_pb2.py").exists():
        from grpc_tools import protoc

        rc = protoc.main(
            [
                "protoc",
                f"--proto_path={ROOT / 'schemas' / 'events' / 'v2'}",
                f"--python_out={PROJECTOR / 'app'}",
                "events.proto",
            ]
        )
        if rc != 0:
            raise RuntimeError("protoc failed")
    _clear_app_modules()
    sys.path.insert(0, str(PROJECTOR))
    try:
        from app import db, events_pb2, projector
    finally:
        sys.path.remove(str(PROJECTOR))
    return db, events_pb2, projector


try:
    import psycopg  # noqa: F401

    if DSN:
        db, events_pb2, projector_module = _load()
        _clear_app_modules()
    SKIP = None if DSN else "PROJECTOR_TEST_DSN not set"
except ImportError as err:  # psycopg or grpc_tools missing
    SKIP = f"projector dependencies missing: {err}"


def transition(incident="inc-1", revision=1, status=1, severity=1, device="gw:02:00:00:00:00:0a"):
    t = events_pb2.DetectionTransition()
    t.envelope.event_id = f"{incident}:{revision}"
    t.envelope.gateway_id = "gw"
    t.envelope.device_key = device
    t.transition_id = f"{incident}:{revision}"
    t.incident_id = incident
    t.revision = revision
    t.status = status
    t.threat_family = 1
    t.rule_id = "port-scan"
    t.rule_version = "v1"
    t.severity = severity
    t.episode_start_unix_millis = 1_760_486_400_000
    t.evidence_event_time_unix_millis = 1_760_486_400_000 + revision * 1000
    t.evidence_ingest_time_unix_millis = 1_760_486_400_003 + revision * 1000
    t.detected_at_unix_millis = 1_760_486_400_010 + revision * 1000
    h = t.horizons.add()
    h.horizon_seconds = 60
    h.value = 20
    h.open_threshold = 20
    h.over_threshold = True
    t.sample_event_ids.extend(["gw/1", "gw/2"])
    return t


class FakeMessage:
    def __init__(self, value, offset):
        self._value, self._offset = value, offset

    def value(self):
        return self._value

    def error(self):
        return None

    def topic(self):
        return "traffic.detections.v2"

    def partition(self):
        return 0

    def offset(self):
        return self._offset


class FakeConsumer:
    """Records commits; can be told to fail the next commit (a crash)."""

    def __init__(self):
        self.committed = []
        self.fail_next_commit = False

    def commit(self, message, asynchronous):
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError("crash before the offset commit")
        self.committed.append(message.offset())

    def poll(self, timeout):
        return None

    def close(self):
        pass


@unittest.skipIf(SKIP, SKIP or "")
class IncidentProjectorTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        self.conn = psycopg.connect(DSN, autocommit=True)
        self.conn.execute("DROP SCHEMA public CASCADE")
        self.conn.execute("CREATE SCHEMA public")
        self.assertEqual(
            db.migrate(self.conn), ["0001_incidents.sql", "0002_gateway_health.sql"]
        )

    def tearDown(self):
        self.conn.close()

    def count(self, table):
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def test_migrations_are_idempotent(self):
        self.assertEqual(db.migrate(self.conn), [])

    def test_repeated_delivery_is_a_no_op(self):
        t = transition()
        first = db.apply_transition(self.conn, t)
        self.assertIsNotNone(first)
        self.assertIsNone(db.apply_transition(self.conn, t))
        self.assertEqual(self.count("incident_transitions"), 1)
        self.assertEqual(self.count("incident_feed"), 1)
        row = self.conn.execute(
            "SELECT status, active, severity, revision FROM incidents WHERE incident_id = 'inc-1'"
        ).fetchone()
        self.assertEqual(row, ("OPEN", True, "LOW", 1))

    def test_stale_revision_does_not_replace_current_state(self):
        db.apply_transition(self.conn, transition(revision=2, status=2, severity=2))
        db.apply_transition(self.conn, transition(revision=1, status=1, severity=1))
        row = self.conn.execute(
            "SELECT revision, status, severity FROM incidents WHERE incident_id = 'inc-1'"
        ).fetchone()
        self.assertEqual(row, (2, "UPDATE", "MEDIUM"))
        # Both revisions are recorded and fed; browsers apply them in order.
        self.assertEqual(self.count("incident_transitions"), 2)
        self.assertEqual(self.count("incident_feed"), 2)

    def test_resolve_marks_the_incident_inactive(self):
        db.apply_transition(self.conn, transition(revision=1, status=1))
        db.apply_transition(self.conn, transition(revision=2, status=4))
        row = self.conn.execute("SELECT status, active FROM incidents").fetchone()
        self.assertEqual(row, ("RESOLVE", False))

    def test_crash_between_db_commit_and_offset_commit_replays_safely(self):
        import psycopg

        consumer = FakeConsumer()
        p = projector_module.Projector(consumer, lambda: psycopg.connect(DSN, autocommit=True))
        msg = FakeMessage(transition().SerializeToString(), offset=7)

        consumer.fail_next_commit = True
        with self.assertRaises(RuntimeError):
            p.handle(msg)  # database committed, offset not
        self.assertEqual(consumer.committed, [])

        # After a restart the same message is delivered again.
        p2 = projector_module.Projector(consumer, lambda: psycopg.connect(DSN, autocommit=True))
        self.assertIsNone(p2.handle(msg))
        self.assertEqual(consumer.committed, [7])
        self.assertEqual(self.count("incident_feed"), 1)
        self.assertEqual(p2.stats["duplicates"], 1)

    def test_malformed_message_is_skipped_and_committed(self):
        import psycopg

        consumer = FakeConsumer()
        p = projector_module.Projector(consumer, lambda: psycopg.connect(DSN, autocommit=True))
        self.assertIsNone(p.handle(FakeMessage(b"\xff\x00garbage", offset=3)))
        self.assertEqual(consumer.committed, [3])
        self.assertEqual(p.stats["malformed"], 1)
        self.assertEqual(self.count("incident_transitions"), 0)

    def test_feed_pruning_keeps_recent_rows(self):
        db.apply_transition(self.conn, transition(incident="old", revision=1))
        db.apply_transition(self.conn, transition(incident="new", revision=1))
        self.conn.execute(
            "UPDATE incident_feed SET committed_at = now() - interval '8 days' WHERE incident_id = 'old'"
        )
        self.assertEqual(db.prune_feed(self.conn), 1)
        left = [r[0] for r in self.conn.execute("SELECT incident_id FROM incident_feed")]
        self.assertEqual(left, ["new"])

    def test_commit_notifies_listeners_with_the_cursor(self):
        import psycopg

        with psycopg.connect(DSN, autocommit=True) as listener:
            listener.execute(f"LISTEN {db.FEED_CHANNEL}")
            cursor = db.apply_transition(self.conn, transition())
            notes = list(listener.notifies(timeout=5, stop_after=1))
        self.assertEqual([n.payload for n in notes], [str(cursor)])

    def test_health_keeps_the_latest_event_per_kind_and_notifies(self):
        import psycopg

        def health(kind, count, last_ms, detail=""):
            h = events_pb2.HealthEvent()
            h.envelope.gateway_id = "gw"
            h.component = "sensor-spool" if kind == 2 else "sensor"
            h.kind = kind
            h.count = count
            h.first_sequence = 10
            h.last_sequence = 20
            h.first_event_time_unix_millis = last_ms - 1000
            h.last_event_time_unix_millis = last_ms
            h.detail = detail
            return h

        with psycopg.connect(DSN, autocommit=True) as listener:
            listener.execute(f"LISTEN {db.HEALTH_CHANNEL}")
            self.assertTrue(db.apply_health(self.conn, health(2, 5, 2_000, "spool full")))
            notes = list(listener.notifies(timeout=5, stop_after=1))
        self.assertEqual([n.payload for n in notes], ["gw"])
        self.assertFalse(db.apply_health(self.conn, health(2, 3, 1_000)), "older event ignored")
        self.assertTrue(db.apply_health(self.conn, health(1, 99, 3_000, "waiting for wlan0")))
        rows = self.conn.execute(
            "SELECT kind, count, detail FROM gateway_health ORDER BY kind"
        ).fetchall()
        self.assertEqual(rows, [("HEARTBEAT", 99, "waiting for wlan0"), ("SPOOL_LOSS", 5, "spool full")])

    def test_feed_payload_carries_latency_fields(self):
        db.apply_transition(self.conn, transition())
        payload = self.conn.execute("SELECT payload FROM incident_feed").fetchone()[0]
        self.assertEqual(payload["transition_id"], "inc-1:1")
        self.assertEqual(payload["status"], "OPEN")
        self.assertIn("evidence_ingest_time_ms", payload)
        self.assertEqual(payload["sample_event_ids"], ["gw/1", "gw/2"])
        committed = self.conn.execute("SELECT committed_at FROM incident_feed").fetchone()[0]
        self.assertLess(dt.datetime.now(dt.timezone.utc) - committed, dt.timedelta(minutes=1))


if __name__ == "__main__":
    unittest.main()
