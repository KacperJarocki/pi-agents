"""
Real unit tests for ML pipeline logic.
Tests cover: FeatureExtractor, risk scoring helpers, behavior alert heuristics,
and run_retention_cleanup — all with real data, no source-inspection.
"""
import sys
import unittest
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
_ML_PATH = str(_REPO / "images" / "ml-pipeline")


def _setup_ml_path():
    """Insert ml-pipeline into sys.path and clear any stale `app` from sys.modules."""
    if _ML_PATH not in sys.path:
        sys.path.insert(0, _ML_PATH)
    for key in list(sys.modules.keys()):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]


def _make_flows(
    device_id: int = 1,
    n: int = 10,
    start: datetime | None = None,
    dst_ips: list[str] | None = None,
    dst_ports: list[int] | None = None,
    dns_queries: list[str | None] | None = None,
    bytes_sent: int = 1000,
    interval_seconds: float = 10.0,
) -> pd.DataFrame:
    """Helper: build a synthetic flows DataFrame."""
    start = start or datetime(2024, 1, 1, 0, 0, 0)
    dst_ips = dst_ips or ["1.2.3.4"] * n
    dst_ports = dst_ports or [443] * n
    dns_queries = dns_queries or [None] * n
    timestamps = [start + timedelta(seconds=i * interval_seconds) for i in range(n)]
    return pd.DataFrame(
        {
            "device_id": [device_id] * n,
            "timestamp": pd.to_datetime(timestamps),
            "src_ip": ["192.168.1.1"] * n,
            "dst_ip": (dst_ips * n)[:n],
            "src_port": [54321] * n,
            "dst_port": (dst_ports * n)[:n],
            "protocol": ["TCP"] * n,
            "bytes_sent": [bytes_sent] * n,
            "bytes_received": [bytes_sent // 2] * n,
            "dns_query": (dns_queries * n)[:n],
            "flags": [{}] * n,
        }
    )


# ──────────────────────────────────────────────────────────────
# FeatureExtractor
# ──────────────────────────────────────────────────────────────

class TestFeatureExtractor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _setup_ml_path()
        from app.ml_core import FeatureExtractor
        cls._FeatureExtractor = FeatureExtractor

    def setUp(self):
        self.extractor = self._FeatureExtractor(bucket_minutes=5)

    def test_empty_dataframe_returns_empty(self):
        result = self.extractor.extract_features(pd.DataFrame())
        self.assertTrue(result.empty)

    def test_single_flow_produces_one_bucket(self):
        flows = _make_flows(n=1, bytes_sent=500)
        result = self.extractor.extract_features(flows)
        self.assertEqual(len(result), 1)
        self.assertEqual(int(result.iloc[0]["total_bytes"]), 750)  # 500 + 250
        self.assertEqual(int(result.iloc[0]["packets"]), 1)

    def test_total_bytes_is_sent_plus_received(self):
        flows = _make_flows(n=4, bytes_sent=1000)
        result = self.extractor.extract_features(flows)
        row = result.iloc[0]
        self.assertEqual(row["total_bytes"], 4 * (1000 + 500))

    def test_unique_destinations_counted(self):
        flows = _make_flows(n=6, dst_ips=["1.1.1.1", "2.2.2.2", "3.3.3.3"])
        result = self.extractor.extract_features(flows)
        self.assertEqual(int(result.iloc[0]["unique_destinations"]), 3)

    def test_unique_ports_counted(self):
        flows = _make_flows(n=6, dst_ports=[80, 443, 8080])
        result = self.extractor.extract_features(flows)
        self.assertEqual(int(result.iloc[0]["unique_ports"]), 3)

    def test_dns_queries_counted(self):
        queries = ["google.com", None, "github.com", None, "openai.com", None]
        flows = _make_flows(n=6, dns_queries=queries)
        result = self.extractor.extract_features(flows)
        self.assertEqual(int(result.iloc[0]["dns_queries"]), 3)

    def test_flows_split_into_two_buckets(self):
        start = datetime(2024, 1, 1, 0, 0, 0)
        # 10 flows in first 5-min bucket, 10 flows in second
        flows1 = _make_flows(n=10, start=start, interval_seconds=20)
        start2 = start + timedelta(minutes=5)
        flows2 = _make_flows(n=10, start=start2, interval_seconds=20)
        combined = pd.concat([flows1, flows2], ignore_index=True)
        result = self.extractor.extract_features(combined)
        self.assertEqual(len(result), 2)

    def test_avg_bytes_per_packet_nonzero(self):
        flows = _make_flows(n=4, bytes_sent=2000)
        result = self.extractor.extract_features(flows)
        self.assertGreater(result.iloc[0]["avg_bytes_per_packet"], 0)

    def test_packet_rate_is_zero_for_single_flow(self):
        """Single flow has zero time span → packet_rate = 0."""
        flows = _make_flows(n=1)
        result = self.extractor.extract_features(flows)
        self.assertEqual(result.iloc[0]["packet_rate"], 0.0)

    def test_packet_rate_nonzero_for_multiple_flows(self):
        flows = _make_flows(n=6, interval_seconds=10)
        result = self.extractor.extract_features(flows)
        self.assertGreater(result.iloc[0]["packet_rate"], 0)

    def test_output_columns_match_feature_columns(self):
        from app.ml_core import FeatureExtractor
        flows = _make_flows(n=4)
        result = self.extractor.extract_features(flows)
        for col in FeatureExtractor.FEATURE_COLUMNS:
            self.assertIn(col, result.columns, f"Missing column: {col}")
# ──────────────────────────────────────────────────────────────
# Risk scoring pure functions
# ──────────────────────────────────────────────────────────────

class TestRiskFromScore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _setup_ml_path()
        import importlib
        cls.mod = importlib.import_module("app.inference")

    def setUp(self):
        pass

    def test_score_well_above_threshold_gives_low_risk(self):
        # score=0.2 (positive, not anomalous), threshold=-0.5
        # margin = threshold - score = -0.7 < 0 → first branch → risk ≤ 35
        risk = self.mod._risk_from_score(0.2, -0.5)
        self.assertLessEqual(risk, 35.0)
        self.assertGreaterEqual(risk, 0.0)

    def test_score_below_threshold_gives_risk_above_35(self):
        # score=-0.8 < threshold=-0.5 → MORE anomalous
        # margin = -0.5 - (-0.8) = 0.3 > 0 → second branch → risk > 35
        risk = self.mod._risk_from_score(-0.8, -0.5)
        self.assertGreater(risk, 35.0)
        self.assertLessEqual(risk, 100.0)

    def test_score_at_threshold_gives_35(self):
        risk = self.mod._risk_from_score(-0.5, -0.5)
        self.assertAlmostEqual(risk, 35.0, places=2)

    def test_very_anomalous_score_approaches_100(self):
        risk = self.mod._risk_from_score(-5.0, -0.5)
        self.assertGreater(risk, 90.0)

    def test_output_always_within_0_100(self):
        for s in [-10.0, -1.0, -0.5, 0.0, 0.5, 1.0, 10.0]:
            risk = self.mod._risk_from_score(s, -0.5)
            self.assertGreaterEqual(risk, 0.0, f"score={s}")
            self.assertLessEqual(risk, 100.0, f"score={s}")


class TestRiskWithContributors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _setup_ml_path()
        import importlib
        cls.mod = importlib.import_module("app.inference")

    def setUp(self):
        pass

    def _make_alert(self, alert_type: str, score: float) -> dict:
        return {
            "alert_type": alert_type,
            "score": score,
            "title": f"Alert {alert_type}",
            "description": "test",
            "evidence": {},
            "severity": "warning",
            "device_id": 1,
            "bucket_start": None,
        }

    def test_no_alerts_returns_zero_behavior_risk(self):
        result = self.mod._risk_with_contributors(ml_risk=20.0, behavior_alerts=[])
        self.assertEqual(result["behavior_risk"], 0.0)
        self.assertEqual(result["protocol_risk"], 0.0)
        self.assertEqual(result["final_risk"], 20.0)

    def test_behavior_risk_capped_at_35(self):
        # Many high-score behavior alerts — should not exceed 35
        alerts = [self._make_alert("dns_burst", 100.0) for _ in range(10)]
        result = self.mod._risk_with_contributors(ml_risk=0.0, behavior_alerts=alerts)
        self.assertLessEqual(result["behavior_risk"], 35.0)

    def test_protocol_risk_capped_at_20(self):
        alerts = [self._make_alert("dns_failure_spike", 100.0) for _ in range(10)]
        result = self.mod._risk_with_contributors(ml_risk=0.0, behavior_alerts=alerts)
        self.assertLessEqual(result["protocol_risk"], 20.0)

    def test_correlation_bonus_capped_at_15(self):
        alerts = [
            self._make_alert("dns_burst", 80.0),
            self._make_alert("port_churn", 80.0),
            self._make_alert("dns_failure_spike", 80.0),
        ]
        result = self.mod._risk_with_contributors(ml_risk=25.0, behavior_alerts=alerts)
        self.assertLessEqual(result["correlation_bonus"], 15.0)

    def test_final_risk_capped_at_100(self):
        alerts = [
            self._make_alert("dns_burst", 100.0),
            self._make_alert("port_churn", 100.0),
            self._make_alert("destination_novelty", 100.0),
            self._make_alert("dns_failure_spike", 100.0),
        ]
        result = self.mod._risk_with_contributors(ml_risk=100.0, behavior_alerts=alerts)
        self.assertLessEqual(result["final_risk"], 100.0)

    def test_reason_summary_populated(self):
        alerts = [self._make_alert("dns_burst", 60.0)]
        result = self.mod._risk_with_contributors(ml_risk=10.0, behavior_alerts=alerts)
        self.assertIsInstance(result["reason_summary"], list)
        self.assertGreater(len(result["reason_summary"]), 0)

    def test_top_reason_is_string(self):
        result = self.mod._risk_with_contributors(ml_risk=10.0, behavior_alerts=[])
        self.assertIsInstance(result["top_reason"], str)


class TestBaselineHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _setup_ml_path()
        import importlib
        cls.mod = importlib.import_module("app.inference")

    def setUp(self):
        pass

    def test_median_empty_returns_default(self):
        self.assertEqual(self.mod._median([], default=99.0), 99.0)

    def test_median_single_value(self):
        self.assertEqual(self.mod._median([5.0]), 5.0)

    def test_median_odd_list(self):
        self.assertEqual(self.mod._median([1.0, 3.0, 5.0]), 3.0)

    def test_median_skips_none(self):
        self.assertEqual(self.mod._median([None, 4.0, None, 6.0]), 5.0)

    def test_percentile_p95(self):
        values = list(range(1, 101))  # 1..100
        p95 = self.mod._percentile([float(v) for v in values], 0.95)
        self.assertAlmostEqual(p95, 95.0, delta=2.0)

    def test_percentile_empty_returns_default(self):
        self.assertEqual(self.mod._percentile([], 0.95, default=42.0), 42.0)

    def test_baseline_stats_keys(self):
        result = self.mod._baseline_stats([1.0, 2.0, 3.0])
        self.assertIn("median", result)
        self.assertIn("p95", result)

    def test_baseline_stats_empty(self):
        result = self.mod._baseline_stats([])
        self.assertEqual(result["median"], 0.0)
        self.assertEqual(result["p95"], 0.0)


# ──────────────────────────────────────────────────────────────
# Behavior alert heuristics
# ──────────────────────────────────────────────────────────────

class TestBuildBehaviorAlerts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _setup_ml_path()
        import importlib
        cls.mod = importlib.import_module("app.inference")
        from app.ml_core import FeatureExtractor
        cls._FeatureExtractor = FeatureExtractor

    def setUp(self):
        self.extractor = self._FeatureExtractor(bucket_minutes=5)

    def _alert_types(self, alerts: list[dict]) -> set[str]:
        return {a["alert_type"] for a in alerts}

    def _make_bucketed(self, flows: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Return (latest_bucket_flows, history_flows) split."""
        features = self.extractor.extract_features(flows)
        if features.empty:
            return pd.DataFrame(), pd.DataFrame()
        # Assign bucket_start back to flows
        bucket_size = f"{self.extractor.bucket_minutes}min"
        flows_b = flows.assign(bucket_start=flows["timestamp"].dt.floor(bucket_size))
        buckets = sorted(flows_b["bucket_start"].unique())
        latest_bucket = flows_b[flows_b["bucket_start"] == buckets[-1]]
        history = flows_b[flows_b["bucket_start"] < buckets[-1]]
        return latest_bucket, history

    # destination_novelty
    def test_destination_novelty_triggered(self):
        """≥2 new IPs (never seen before) and ratio ≥0.4 → destination_novelty."""
        history = _make_flows(n=20, dst_ips=["1.1.1.1", "2.2.2.2"])
        start_latest = datetime(2024, 1, 1, 0, 30)
        novel_ips = [f"10.0.0.{i}" for i in range(1, 7)]  # 6 brand-new IPs
        latest = _make_flows(n=6, start=start_latest, dst_ips=novel_ips)
        history_b = history.assign(bucket_start=history["timestamp"].dt.floor("5min"))
        history_features = self.extractor.extract_features(history)
        latest_b = latest.assign(bucket_start=latest["timestamp"].dt.floor("5min"))

        alerts = self.mod._build_behavior_alerts(1, latest_b, history_features, history_b)
        self.assertIn("destination_novelty", self._alert_types(alerts))

    def test_destination_novelty_not_triggered_when_ips_known(self):
        """All destinations already seen → no novelty alert."""
        base_ips = ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
        history = _make_flows(n=30, dst_ips=base_ips)
        start_latest = datetime(2024, 1, 1, 0, 30)
        latest = _make_flows(n=6, start=start_latest, dst_ips=base_ips)
        history_b = history.assign(bucket_start=history["timestamp"].dt.floor("5min"))
        history_features = self.extractor.extract_features(history)
        latest_b = latest.assign(bucket_start=latest["timestamp"].dt.floor("5min"))

        alerts = self.mod._build_behavior_alerts(1, latest_b, history_features, history_b)
        self.assertNotIn("destination_novelty", self._alert_types(alerts))

    # dns_burst
    def test_dns_burst_triggered(self):
        """Many diverse DNS queries well above baseline → dns_burst."""
        # History: almost no DNS (baseline median ≈ 0)
        history = _make_flows(n=20, dns_queries=[None] * 20)
        start_latest = datetime(2024, 1, 1, 0, 30)
        # 20 different domains in latest bucket — far above baseline=0
        domains = [f"domain{i}.com" for i in range(20)]
        latest = _make_flows(n=20, start=start_latest, dns_queries=domains)
        history_b = history.assign(bucket_start=history["timestamp"].dt.floor("5min"))
        history_features = self.extractor.extract_features(history)
        latest_b = latest.assign(bucket_start=latest["timestamp"].dt.floor("5min"))

        alerts = self.mod._build_behavior_alerts(1, latest_b, history_features, history_b)
        self.assertIn("dns_burst", self._alert_types(alerts))

    # port_churn
    def test_port_churn_triggered_by_many_new_ports(self):
        """≥5 never-seen ports → port_churn."""
        history = _make_flows(n=20, dst_ports=[80, 443])
        start_latest = datetime(2024, 1, 1, 0, 30)
        new_ports = [22, 23, 25, 3389, 5900, 6379, 27017, 5432]
        latest = _make_flows(n=8, start=start_latest, dst_ports=new_ports)
        history_b = history.assign(bucket_start=history["timestamp"].dt.floor("5min"))
        history_features = self.extractor.extract_features(history)
        latest_b = latest.assign(bucket_start=latest["timestamp"].dt.floor("5min"))

        alerts = self.mod._build_behavior_alerts(1, latest_b, history_features, history_b)
        self.assertIn("port_churn", self._alert_types(alerts))

    # beaconing_suspected
    def test_beaconing_triggered_by_regular_small_packets(self):
        """Regular 30s interval, small bytes, same dst_ip → beaconing_suspected."""
        start = datetime(2024, 1, 1, 0, 0, 0)
        # History: 20 regular pings every 30s to same IP
        history_ts = [start + timedelta(seconds=i * 30) for i in range(20)]
        n = len(history_ts)
        beacon_ip = "10.0.0.1"
        history = pd.DataFrame({
            "device_id": [1] * n,
            "timestamp": pd.to_datetime(history_ts),
            "src_ip": ["192.168.1.1"] * n,
            "dst_ip": [beacon_ip] * n,
            "src_port": [0] * n,
            "dst_port": [80] * n,
            "protocol": ["TCP"] * n,
            "bytes_sent": [100] * n,  # small
            "bytes_received": [50] * n,
            "dns_query": [None] * n,
            "flags": [{}] * n,
        })
        # Latest bucket also contacts the same beacon_ip so it passes the filter
        start_latest = history_ts[-1] + timedelta(seconds=30)
        latest = pd.DataFrame({
            "device_id": [1],
            "timestamp": pd.to_datetime([start_latest]),
            "src_ip": ["192.168.1.1"],
            "dst_ip": [beacon_ip],
            "src_port": [0],
            "dst_port": [80],
            "protocol": ["TCP"],
            "bytes_sent": [100],
            "bytes_received": [50],
            "dns_query": [None],
            "flags": [{}],
        })
        history_b = history.assign(bucket_start=history["timestamp"].dt.floor("5min"))
        history_features = self.extractor.extract_features(history)
        latest_b = latest.assign(bucket_start=latest["timestamp"].dt.floor("5min"))

        alerts = self.mod._build_behavior_alerts(1, latest_b, history_features, history_b)
        self.assertIn("beaconing_suspected", self._alert_types(alerts))

    def test_no_alerts_on_empty_latest_bucket(self):
        alerts = self.mod._build_behavior_alerts(1, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        self.assertEqual(alerts, [])

    def test_alert_score_within_range(self):
        """All alert scores must be between 0 and 100."""
        history = _make_flows(n=20, dst_ips=["1.1.1.1"])
        start_latest = datetime(2024, 1, 1, 0, 30)
        novel_ips = [f"10.0.0.{i}" for i in range(1, 7)]
        latest = _make_flows(n=6, start=start_latest, dst_ips=novel_ips)
        history_b = history.assign(bucket_start=history["timestamp"].dt.floor("5min"))
        history_features = self.extractor.extract_features(history)
        latest_b = latest.assign(bucket_start=latest["timestamp"].dt.floor("5min"))

        alerts = self.mod._build_behavior_alerts(1, latest_b, history_features, history_b)
        for alert in alerts:
            self.assertGreaterEqual(alert["score"], 0.0, alert["alert_type"])
            self.assertLessEqual(alert["score"], 100.0, alert["alert_type"])

    def test_alert_has_required_keys(self):
        history = _make_flows(n=20, dst_ips=["1.1.1.1"])
        start_latest = datetime(2024, 1, 1, 0, 30)
        novel_ips = [f"10.0.0.{i}" for i in range(1, 7)]
        latest = _make_flows(n=6, start=start_latest, dst_ips=novel_ips)
        history_b = history.assign(bucket_start=history["timestamp"].dt.floor("5min"))
        history_features = self.extractor.extract_features(history)
        latest_b = latest.assign(bucket_start=latest["timestamp"].dt.floor("5min"))

        alerts = self.mod._build_behavior_alerts(1, latest_b, history_features, history_b)
        required = {"device_id", "alert_type", "severity", "score", "title", "description", "evidence"}
        for alert in alerts:
            self.assertTrue(required.issubset(alert.keys()), f"Missing keys in {alert['alert_type']}")


# ──────────────────────────────────────────────────────────────
# run_retention_cleanup (in-memory SQLite)
# ──────────────────────────────────────────────────────────────

class TestRetentionCleanup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import aiosqlite

        _setup_ml_path()
        import importlib
        self.mod = importlib.import_module("app.inference")
        # Patch DB_PATH to in-memory DB using a temp file
        import tempfile, os
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._db_path = self._tmp.name
        self._tmp.close()

        # Patch the module's DB_PATH
        import app.ml_core as ml_core
        self._orig_db_path = ml_core.DB_PATH
        ml_core.DB_PATH = self._db_path

        # Patch inference module's reference too
        import app.inference as inf_mod
        self._orig_inf_db = inf_mod.DB_PATH
        inf_mod.DB_PATH = self._db_path

        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS traffic_flows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER,
                    timestamp TEXT,
                    src_ip TEXT, dst_ip TEXT, src_port INTEGER, dst_port INTEGER,
                    protocol TEXT, bytes_sent INTEGER, bytes_received INTEGER,
                    dns_query TEXT, flags TEXT
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS anomalies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER,
                    anomaly_type TEXT,
                    severity TEXT,
                    score REAL,
                    description TEXT,
                    features TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    resolved INTEGER DEFAULT 0
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS device_behavior_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id INTEGER,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                    bucket_start TEXT,
                    alert_type TEXT,
                    severity TEXT,
                    score REAL,
                    title TEXT,
                    description TEXT,
                    evidence TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    resolved INTEGER DEFAULT 0
                )
            """)
            await conn.commit()

    async def asyncTearDown(self):
        import app.ml_core as ml_core
        import app.inference as inf_mod
        import os
        ml_core.DB_PATH = self._orig_db_path
        inf_mod.DB_PATH = self._orig_inf_db
        os.unlink(self._db_path)

    async def _insert_flows(self, timestamps: list[str]):
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as conn:
            for ts in timestamps:
                await conn.execute(
                    "INSERT INTO traffic_flows (device_id, timestamp, src_ip, dst_ip, src_port, dst_port, protocol, bytes_sent, bytes_received) VALUES (1, ?, '1.1.1.1', '2.2.2.2', 1234, 80, 'TCP', 100, 50)",
                    (ts,),
                )
            await conn.commit()

    async def _count_flows(self) -> int:
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute("SELECT COUNT(*) FROM traffic_flows")
            row = await cur.fetchone()
            return row[0]

    async def _insert_anomalies(self, timestamps_resolved: list[tuple[str, int]]):
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as conn:
            for ts, resolved in timestamps_resolved:
                await conn.execute(
                    "INSERT INTO anomalies (device_id, anomaly_type, severity, score, description, features, timestamp, resolved) VALUES (1, 'test', 'warning', 0.5, 'desc', '{}', ?, ?)",
                    (ts, resolved),
                )
            await conn.commit()

    async def test_old_flows_deleted(self):
        old_ts = "2020-01-01 00:00:00"
        new_ts = "2099-12-31 00:00:00"
        await self._insert_flows([old_ts, new_ts])

        await self.mod.run_retention_cleanup(flows_days=7, alerts_days=14, anomaly_resolve_hours=48)
        count = await self._count_flows()
        self.assertEqual(count, 1)  # Only new_ts survives

    async def test_recent_flows_kept(self):
        new_ts = "2099-12-31 00:00:00"
        await self._insert_flows([new_ts, new_ts, new_ts])

        await self.mod.run_retention_cleanup(flows_days=7, alerts_days=14, anomaly_resolve_hours=48)
        count = await self._count_flows()
        self.assertEqual(count, 3)

    async def test_old_anomalies_auto_resolved(self):
        import aiosqlite
        old_ts = "2020-01-01 00:00:00"
        new_ts = "2099-12-31 00:00:00"
        await self._insert_anomalies([(old_ts, 0), (new_ts, 0)])

        await self.mod.run_retention_cleanup(flows_days=7, alerts_days=14, anomaly_resolve_hours=48)

        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute("SELECT timestamp, resolved FROM anomalies ORDER BY timestamp")
            rows = await cur.fetchall()
        resolved_map = {row[0]: row[1] for row in rows}
        self.assertEqual(resolved_map[old_ts], 1)
        self.assertEqual(resolved_map[new_ts], 0)

    async def test_already_resolved_anomalies_untouched(self):
        import aiosqlite
        old_ts = "2020-01-01 00:00:00"
        await self._insert_anomalies([(old_ts, 1)])  # already resolved

        await self.mod.run_retention_cleanup(flows_days=7, alerts_days=14, anomaly_resolve_hours=48)
        async with aiosqlite.connect(self._db_path) as conn:
            cur = await conn.execute("SELECT resolved FROM anomalies")
            row = await cur.fetchone()
        self.assertEqual(row[0], 1)


if __name__ == "__main__":
    unittest.main()
