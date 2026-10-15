import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class RustSensorSourceTests(unittest.TestCase):
    def test_sensor_core_needs_libpcap_only_for_live_capture(self):
        cargo = (ROOT / "sensors" / "rust-sensor" / "Cargo.toml").read_text()
        source = (ROOT / "sensors" / "rust-sensor" / "src" / "lib.rs").read_text()
        self.assertIn('edition = "2021"', cargo)
        self.assertIn('live = ["dep:pcap"]', cargo)
        self.assertIn("optional = true", cargo)
        self.assertIn("pub struct FlowTable", source)
        self.assertIn("pub fn flush_due", source)
        self.assertIn("UpsertResult::Evicted", source)
        self.assertIn("DEFAULT_IDLE_US: i64 = 1_000_000", source)
        self.assertIn("DEFAULT_ACTIVE_US: i64 = 1_000_000", source)

    def test_sensor_publishes_through_a_bounded_spool_with_idempotent_kafka(self):
        crate = ROOT / "sensors" / "rust-sensor"
        cargo = (crate / "Cargo.toml").read_text()
        kafka = (crate / "src" / "kafka.rs").read_text()
        spool = (crate / "src" / "spool.rs").read_text()
        forward = (crate / "src" / "forward.rs").read_text()
        self.assertIn('kafka = ["dep:rdkafka"]', cargo)
        self.assertIn('.set("acks", "all")', kafka)
        self.assertIn('.set("enable.idempotence", "true")', kafka)
        self.assertIn('.set("linger.ms", "20")', kafka)
        self.assertIn("SEQUENCE_BLOCK", spool)
        self.assertIn("pub struct Loss", spool)
        self.assertIn('FLOWS_TOPIC: &str = "traffic.flows.v2"', forward)
        self.assertIn('HEALTH_TOPIC: &str = "traffic.health.v2"', forward)
        self.assertIn("HealthKind::SpoolLoss", forward)

    def test_sensor_has_pcap_fixtures_for_each_scenario(self):
        fixtures = ROOT / "sensors" / "rust-sensor" / "tests" / "fixtures"
        for name in (
            "tcp_established.pcap",
            "tcp_rejected.pcap",
            "tcp_unanswered.pcap",
            "udp_dns.pcap",
            "icmp.pcap",
            "unattributed.pcap",
            "leases.txt",
            "generate.py",
        ):
            self.assertTrue((fixtures / name).exists(), name)

    def test_sensor_core_does_not_hide_overflow(self):
        readme = (ROOT / "sensors" / "rust-sensor" / "README.md").read_text()
        self.assertIn("UpsertResult::Evicted", readme)

    def test_arm64_sensor_workflow_checks_format_tests_and_target(self):
        workflow = (ROOT / ".github" / "workflows" / "rust-sensor.yml").read_text()
        self.assertIn("aarch64-unknown-linux-gnu", workflow)
        self.assertIn("cargo fmt", workflow)
        self.assertIn("cargo test", workflow)
        self.assertIn("cargo check", workflow)
        self.assertIn("--features live", workflow)
        self.assertIn("--features kafka,live", workflow)


if __name__ == "__main__":
    unittest.main()
