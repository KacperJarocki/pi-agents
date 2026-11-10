"""Incident API against a real PostgreSQL (snapshot, changes, live WebSocket).

Needs PROJECTOR_TEST_DSN plus the projector and incident-api requirements
(including requirements-dev.txt); skipped otherwise.
"""

import os
import pathlib
import sys
import tempfile
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
        self.leases = [
            {
                "mac_address": "02:00:00:00:00:0A",
                "ip_address": "192.168.50.134",
                "hostname": "zero",
                "lease_expires_at": "1794379528",
            },
            {
                "mac_address": "02:00:00:00:00:0b",
                "ip_address": "192.168.50.137",
                "hostname": "iPhone",
                "lease_expires_at": "1794378166",
            },
        ]

        async def fetch():
            if self.leases is None:
                raise OSError("unreachable")
            return self.leases

        self.lease_source = api_main.Leases("http://gateway-agent:7000", fetch=fetch)
        # Stand-in for the built UI (images/incident-api/ui/dist).
        self.static = tempfile.TemporaryDirectory()
        pages = pathlib.Path(self.static.name)
        (pages / "live").mkdir()
        (pages / "index.html").write_text("<title>Devices</title>")
        (pages / "live" / "index.html").write_text("<title>Live incidents</title>")
        self.client_cm = TestClient(api_main.create_app(DSN, leases=self.lease_source, static_dir=pages))
        self.client = self.client_cm.__enter__()

    def tearDown(self):
        self.client_cm.__exit__(None, None, None)
        self.static.cleanup()
        self.conn.close()

    def apply(self, incident, revision, status=1):
        return proj.db.apply_transition(self.conn, proj.transition(incident=incident, revision=revision, status=status))

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

    def test_live_page_is_served_and_root_shows_devices(self):
        page = self.client.get("/live/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Live incidents", page.text)
        root = self.client.get("/", follow_redirects=False)
        self.assertEqual(root.status_code, 200)
        self.assertIn("<title>Devices</title>", root.text)
        self.assertEqual(self.client.get("/api/v2/health").status_code, 200, "API routes win over the UI mount")

    def test_devices_join_features_incidents_and_leases_by_mac(self):
        proj.db.apply_features(self.conn, proj.feature_update(window_end=5_000, flows=4))
        proj.db.apply_features(self.conn, proj.feature_update(horizon=900, window_end=6_000, flows=40))
        self.apply("a", 1)  # active, device gw:02:00:00:00:00:0a
        self.apply("b", 1)
        self.apply("b", 2, status=4)  # resolved
        proj.db.apply_features(self.conn, proj.feature_update(device="gw:02:00:00:00:00:0c", window_end=1_000))
        body = self.client.get("/api/v2/devices").json()
        self.assertTrue(body["leases"]["available"])
        by_mac = {d["mac"]: d for d in body["devices"]}
        self.assertEqual(set(by_mac), {"02:00:00:00:00:0a", "02:00:00:00:00:0b", "02:00:00:00:00:0c"})
        zero = by_mac["02:00:00:00:00:0a"]
        self.assertEqual(body["devices"][0]["mac"], "02:00:00:00:00:0a", "devices with active incidents first")
        self.assertEqual(
            (zero["hostname"], zero["ip"], zero["device_key"]), ("zero", "192.168.50.134", "gw:02:00:00:00:00:0a")
        )
        self.assertEqual(zero["features"]["60"]["flows"], 4)
        self.assertEqual(zero["features"]["900"]["flows"], 40)
        self.assertEqual(zero["last_seen_ms"], 6_000)
        self.assertEqual(
            zero["incidents"],
            {"active": 1, "total": 2, "last_detected_ms": zero["incidents"]["last_detected_ms"], "severity": "LOW"},
        )
        self.assertEqual(zero["lease_expires_at_ms"], 1_794_379_528_000)
        phone = by_mac["02:00:00:00:00:0b"]
        self.assertIsNone(phone["device_key"], "leased but never seen by the sensor")
        self.assertIsNone(by_mac["02:00:00:00:00:0c"]["hostname"], "seen but no lease")

    def test_devices_still_listed_when_the_gateway_agent_is_down(self):
        self.leases = None
        proj.db.apply_features(self.conn, proj.feature_update())
        body = self.client.get("/api/v2/devices").json()
        self.assertFalse(body["leases"]["available"])
        self.assertIn("unavailable", body["leases"]["error"])
        self.assertEqual([d["device_key"] for d in body["devices"]], ["gw:02:00:00:00:00:0a"])

    def test_incident_history_per_device_and_transitions(self):
        self.apply("a", 1)
        self.apply("a", 2, status=4)
        proj.db.apply_transition(self.conn, proj.transition(incident="other", device="gw:02:00:00:00:00:0b"))
        body = self.client.get("/api/v2/incidents", params={"active": "false", "device": "gw:02:00:00:00:00:0a"}).json()
        self.assertEqual([i["incident_id"] for i in body["incidents"]], ["a"])
        steps = self.client.get("/api/v2/incidents/a/transitions").json()["transitions"]
        self.assertEqual([(s["revision"], s["status"]) for s in steps], [(1, "OPEN"), (2, "RESOLVE")])
        self.assertEqual(self.client.get("/api/v2/incidents/nope/transitions").status_code, 404)

    def test_websocket_works_through_a_real_uvicorn_server(self):
        """TestClient does not need a WebSocket library; uvicorn does."""
        import socket

        import uvicorn
        from websockets.sync.client import connect

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(api_main.create_app(DSN), host="127.0.0.1", port=port, log_level="warning")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                if server.started:
                    break
                time.sleep(0.05)
            self.apply("a", 1)
            with connect(f"ws://127.0.0.1:{port}/ws/v2/incidents?after=0", open_timeout=5) as ws:
                import json

                kinds = [json.loads(ws.recv(timeout=5))["type"] for _ in range(3)]
            self.assertEqual(kinds, ["health", "change", "live"])
        finally:
            server.should_exit = True
            thread.join(timeout=5)

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
