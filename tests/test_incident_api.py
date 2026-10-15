"""Incident API against a real PostgreSQL (snapshot, changes, live WebSocket).

Needs PROJECTOR_TEST_DSN plus the projector and incident-api requirements
(including requirements-dev.txt); skipped otherwise.
"""

import os
import pathlib
import sys
import threading
import time
import unittest

from tests import test_incident_projector as proj

ROOT = pathlib.Path(__file__).resolve().parents[1]
API = ROOT / "images" / "incident-api"
DSN = os.environ.get("PROJECTOR_TEST_DSN")

SKIP = proj.SKIP
if not SKIP:
    try:
        proj._clear_app_modules()
        sys.path.insert(0, str(API))
        try:
            from app import main as api_main
        finally:
            sys.path.remove(str(API))
            proj._clear_app_modules()
        from fastapi.testclient import TestClient
    except ImportError as err:
        SKIP = f"incident-api dependencies missing: {err}"


@unittest.skipIf(SKIP, SKIP or "")
class IncidentApiTests(unittest.TestCase):
    def setUp(self):
        import psycopg

        self.conn = psycopg.connect(DSN, autocommit=True)
        self.conn.execute("DROP SCHEMA public CASCADE")
        self.conn.execute("CREATE SCHEMA public")
        proj.db.migrate(self.conn)
        self.client_cm = TestClient(api_main.create_app(DSN))
        self.client = self.client_cm.__enter__()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.conn.close()

    def apply(self, incident, revision, status=1):
        return proj.db.apply_transition(
            self.conn, proj.transition(incident=incident, revision=revision, status=status)
        )

    def test_snapshot_returns_active_incidents_with_a_cursor(self):
        self.apply("a", 1)
        self.apply("b", 1)
        self.apply("b", 2, status=4)  # resolved
        body = self.client.get("/api/v2/incidents").json()
        self.assertEqual(body["cursor"], 3)
        self.assertEqual([i["incident_id"] for i in body["incidents"]], ["a"])
        all_ = self.client.get("/api/v2/incidents", params={"active": "false"}).json()
        self.assertEqual(sorted(i["incident_id"] for i in all_["incidents"]), ["a", "b"])

    def test_changes_after_a_cursor_are_ordered_and_paged(self):
        for r in range(1, 6):
            self.apply("a", r, status=1 if r == 1 else 2)
        page = self.client.get("/api/v2/incidents/changes", params={"after": 1, "limit": 2}).json()
        self.assertEqual([c["cursor"] for c in page["changes"]], [2, 3])
        self.assertEqual(page["cursor"], 3)
        rest = self.client.get("/api/v2/incidents/changes", params={"after": 3}).json()
        self.assertEqual([c["revision"] for c in rest["changes"]], [4, 5])

    def test_expired_cursor_asks_for_a_snapshot(self):
        for r in range(1, 4):
            self.apply("a", r)
        self.conn.execute("DELETE FROM incident_feed WHERE cursor < 3")  # pruned
        resp = self.client.get("/api/v2/incidents/changes", params={"after": 0})
        self.assertEqual(resp.status_code, 410)
        ok = self.client.get("/api/v2/incidents/changes", params={"after": 2})
        self.assertEqual(ok.status_code, 200)

    def receive_until(self, ws, kind):
        while True:
            msg = ws.receive_json()
            if msg["type"] == kind:
                return msg

    def test_websocket_catch_up_then_live_in_order(self):
        self.apply("a", 1)
        self.apply("a", 2, status=2)
        with self.client.websocket_connect("/ws/v2/incidents?after=0") as ws:
            self.assertEqual(ws.receive_json()["type"], "health")
            caught_up = [ws.receive_json() for _ in range(2)]
            self.assertEqual([m["cursor"] for m in caught_up], [1, 2])
            self.assertEqual(ws.receive_json(), {"type": "live", "cursor": 2})

            def produce():
                for r in range(3, 7):
                    self.apply("a", r, status=2)
                    time.sleep(0.05)

            writer = threading.Thread(target=produce)
            writer.start()
            live = []
            while len(live) < 4:
                msg = ws.receive_json()
                if msg["type"] == "change":
                    live.append(msg["cursor"])
            writer.join()
        self.assertEqual(live, [3, 4, 5, 6], "no gaps, no duplicates, in order")

    def test_reconnect_with_cursor_resumes_after_it(self):
        for r in range(1, 5):
            self.apply("a", r, status=1 if r == 1 else 2)
        with self.client.websocket_connect("/ws/v2/incidents?after=2") as ws:
            ws.receive_json()  # health
            got = [ws.receive_json()["cursor"] for _ in range(2)]
            self.assertEqual(got, [3, 4])
            self.assertEqual(ws.receive_json()["type"], "live")

    def test_expired_cursor_on_websocket_resets_to_a_snapshot(self):
        for r in range(1, 4):
            self.apply("a", r, status=1 if r == 1 else 2)
        self.conn.execute("DELETE FROM incident_feed WHERE cursor < 3")
        with self.client.websocket_connect("/ws/v2/incidents?after=0") as ws:
            ws.receive_json()  # health
            self.assertEqual(ws.receive_json()["type"], "reset")
            snap = ws.receive_json()
            self.assertEqual(snap["type"], "snapshot")
            self.assertEqual(snap["cursor"], 3)
            self.assertEqual(ws.receive_json(), {"type": "live", "cursor": 3})

    def test_health_summary_is_pushed_on_connect_and_on_change(self):
        h = proj.events_pb2.HealthEvent()
        h.envelope.gateway_id = "gw"
        h.component = "sensor"
        h.kind = 1
        h.count = 10
        h.last_event_time_unix_millis = 1_000
        h.detail = "waiting for wlan0"
        proj.db.apply_health(self.conn, h)
        with self.client.websocket_connect("/ws/v2/incidents") as ws:
            first = ws.receive_json()
            self.assertEqual(first["gateways"][0]["detail"], "waiting for wlan0")
            self.assertEqual(ws.receive_json()["type"], "snapshot")
            self.assertEqual(ws.receive_json()["type"], "live")
            h.count = 20
            h.last_event_time_unix_millis = 2_000
            h.detail = ""
            proj.db.apply_health(self.conn, h)
            update = self.receive_until(ws, "health")
            self.assertEqual(update["gateways"][0]["count"], 20)


if __name__ == "__main__":
    unittest.main()
