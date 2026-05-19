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
        self.assertIn("probes.jsonl", src)
        self.assertIn("summary.json", src)
        self.assertIn("port_churn", src)

    def test_port_sweep_wrapper_exists(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "port-sweep.sh").read_text()

        self.assertIn("port-sweep.py", src)
        self.assertIn("$@", src)


if __name__ == "__main__":
    unittest.main()
