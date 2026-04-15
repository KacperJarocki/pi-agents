import unittest


class TestCollectorMvpSources(unittest.TestCase):
    def test_tcpdump_command_keeps_root_user(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "collector" / "app" / "collector.py").read_text()

        self.assertIn('"-Z", "root"', src)

    def test_collector_has_capture_pipeline_logging(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "collector" / "app" / "collector.py").read_text()

        self.assertIn('"tcpdump_finished"', src)
        self.assertIn('"pcap_file_stats"', src)
        self.assertIn('"tshark_finished"', src)
        self.assertIn('"pcap_processed"', src)
        self.assertIn('"flush_started"', src)

    def test_collector_uses_small_batch_defaults_for_mvp(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "collector" / "app" / "__main__.py").read_text()

        self.assertIn('BATCH_SIZE", "25"', src)
        self.assertIn('CAPTURE_PACKET_COUNT', src)
        self.assertIn('CAPTURE_TIMEOUT', src)

    def test_collector_k8s_manifest_sets_mvp_capture_env(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "k8s" / "gateway" / "collector-deployment.yaml").read_text()

        self.assertIn('name: BATCH_SIZE', src)
        self.assertIn('value: "25"', src)
        self.assertIn('name: CAPTURE_PACKET_COUNT', src)
        self.assertIn('name: CAPTURE_TIMEOUT', src)


if __name__ == "__main__":
    unittest.main()
