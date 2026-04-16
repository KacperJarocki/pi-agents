import unittest


class TestTrafficScriptSources(unittest.TestCase):
    def test_generate_anomaly_traffic_script_exists(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "generate-anomaly-traffic.sh").read_text()

        self.assertIn('MODE="${1:-burst}"', src)
        self.assertIn('run_burst()', src)
        self.assertIn('run_spike()', src)
        self.assertIn('run_mix()', src)
        self.assertIn('curl -L --fail --silent --output /dev/null', src)


if __name__ == "__main__":
    unittest.main()
