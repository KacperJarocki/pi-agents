import unittest


class TestCollectorMvpSources(unittest.TestCase):
    def test_tcpdump_command_does_not_force_privilege_switch(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "collector" / "app" / "collector.py").read_text()

        self.assertNotIn('"-Z", "root"', src)

    def test_collector_has_capture_pipeline_logging(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "collector" / "app" / "collector.py").read_text()

        self.assertIn('"tcpdump_finished"', src)
        self.assertIn('"pcap_file_stats"', src)
        self.assertIn('"tshark_finished"', src)
        self.assertIn('"pcap_processed"', src)
        self.assertIn('"flush_started"', src)

    def test_collector_uses_lease_based_device_resolution(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "collector" / "app" / "collector.py").read_text()

        self.assertIn("def _read_lease_map", src)
        self.assertIn("device_resolution_from_lease", src)
        self.assertIn("def _resolve_client_identity", src)
        self.assertIn('flow["device_ip"]', src)

    def test_collector_filters_invalid_device_mac_identities(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "collector" / "app" / "collector.py").read_text()

        self.assertIn('"ff:ff:ff:ff:ff:ff"', src)
        self.assertIn('first_octet & 1', src)


    def test_collector_uses_live_view_capture_defaults(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "collector" / "app" / "__main__.py").read_text()

        self.assertIn('BATCH_SIZE", "150"', src)
        self.assertIn('FLUSH_INTERVAL", "2"', src)
        self.assertIn('CAPTURE_PACKET_COUNT', src)
        self.assertIn('CAPTURE_TIMEOUT', src)
        self.assertIn('SNAPLEN', src)
        self.assertIn('MAX_BUFFER_SIZE', src)

    def test_collector_k8s_manifest_sets_mvp_capture_env(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "k8s" / "gateway" / "collector-deployment.yaml").read_text()

        self.assertIn('name: BATCH_SIZE', src)
        self.assertIn('value: "150"', src)
        self.assertIn('name: FLUSH_INTERVAL', src)
        self.assertIn('name: CAPTURE_PACKET_COUNT', src)
        self.assertIn('name: CAPTURE_TIMEOUT', src)
        self.assertIn('name: SNAPLEN', src)
        self.assertIn('name: MAX_BUFFER_SIZE', src)
        self.assertIn('name: LAN_SUBNET_CIDR', src)
        self.assertIn('name: LEASE_FILE_PATH', src)
        self.assertIn('mountPath: /gateway-state', src)


if __name__ == "__main__":
    unittest.main()
