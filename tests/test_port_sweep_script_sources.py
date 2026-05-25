import unittest
from pathlib import Path


class TestPortSweepScriptSources(unittest.TestCase):
    def test_port_sweep_script_exists_and_has_research_profiles(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "port-sweep.py").read_text()

        self.assertIn('"positive"', src)
        self.assertIn('"negative"', src)      
        self.assertIn('"borderline"', src)
        self.assertIn('"aggressive"', src)
        self.assertIn("socket.create_connection", src)
        self.assertIn("duration_seconds", src)
        self.assertIn("--duration", src)
        self.assertIn("--seed", src)
        self.assertIn("--targets-api", src)
        self.assertIn("active_only", src)
        self.assertIn("urlopen", src)
        self.assertIn("markers.jsonl", src)
        self.assertIn("SIGINT", src)
        self.assertIn("probes.jsonl", src)
        self.assertIn("summary.json", src)
        self.assertIn("port_churn", src)

    def test_port_sweep_wrapper_exists(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "port-sweep.sh").read_text()

        self.assertIn("port-sweep.py", src)
        self.assertIn("$@", src)

    def test_research_runner_orchestrates_protocol(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "research-traffic-runner.py").read_text()
        wrapper = (repo / "scripts" / "research-traffic-runner.sh").read_text()

        self.assertIn("DEFAULT_PHASES", src)
        self.assertIn('"normal"', src)
        self.assertIn('"negative"', src)
        self.assertIn('"borderline"', src)
        self.assertIn('"positive"', src)
        self.assertIn('"slow"', src)
        self.assertIn('"aggressive"', src)
        self.assertIn("iot-device-emulator.py", src)
        self.assertIn("port-sweep.py", src)
        self.assertIn("markers.jsonl", src)
        self.assertIn("manifest.json", src)
        self.assertIn("summary.json", src)
        self.assertIn("research-traffic-runner.py", wrapper)


if __name__ == "__main__":
    unittest.main()
