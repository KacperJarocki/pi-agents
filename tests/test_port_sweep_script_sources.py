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
        self.assertIn("--discover-subnet", src)
        self.assertIn("ThreadPoolExecutor", src)
        self.assertIn("active_only", src)
        self.assertIn("urlopen", src)
        self.assertIn("markers.jsonl", src)
        self.assertIn("SIGINT", src)
        self.assertIn("probes.jsonl", src)
        self.assertIn("summary.json", src)
        self.assertIn("port_churn", src)
        self.assertIn("192.168.50.1", src)
        self.assertIn("ML port-diversity features", src)
        self.assertIn("27017", src)

    def test_port_sweep_wrapper_exists(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "port-sweep.sh").read_text()

        self.assertIn("port-sweep.py", src)
        self.assertIn("$@", src)

    def test_research_runner_orchestrates_protocol(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "research-traffic-runner.py").read_text()
        wrapper = (repo / "scripts" / "research-traffic-runner.sh").read_text()
        shortcut = (repo / "research.py").read_text()

        self.assertIn("DEFAULT_PHASES", src)
        self.assertIn('DEFAULT_PHASES = ["negative", "borderline", "positive", "slow", "aggressive"]', src)
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
        self.assertIn("Research run summary", src)
        self.assertIn("checks", src)
        self.assertIn("timeline_bar", src)
        self.assertIn("started_at_local", src)
        self.assertIn("duration_human", src)
        self.assertIn("--sweep-duration", src)
        self.assertIn("--preset", src)
        self.assertIn('"balanced35"', src)
        self.assertIn('"negative": 10', src)
        self.assertIn('"positive": 10', src)
        self.assertIn('"borderline": 5', src)
        self.assertIn('"slow": 5', src)
        self.assertIn('"aggressive": 5', src)
        self.assertIn("--detach", src)
        self.assertIn("research.log", src)
        self.assertIn("status.json", src)
        self.assertIn('run_dir / "pid"', src)
        self.assertIn("estimated_duration_seconds", src)
        self.assertIn("research-traffic-runner.py", wrapper)
        self.assertIn("research-traffic-runner.py", shortcut)
        self.assertIn("DEFAULT_RESEARCH_ARGS", shortcut)
        self.assertIn('"--preset", "balanced35"', shortcut)
        self.assertIn('"--gap", "5m"', shortcut)
        self.assertIn('"--shuffle-phases"', shortcut)
        self.assertIn('"--detach"', shortcut)
        self.assertIn("runpy.run_path", shortcut)
        self.assertIn("--discover-subnet", src)
        self.assertIn("--no-discover", src)
        self.assertIn("192.168.50.1", src)


if __name__ == "__main__":
    unittest.main()
