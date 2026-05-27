import unittest
from pathlib import Path


class TestModelBacktestResearch(unittest.TestCase):
    def test_backtest_supports_research_windows_and_metrics(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "model-backtest.py").read_text()

        self.assertIn("--windows-file", src)
        self.assertIn("detection_delay_seconds", src)
        self.assertIn("false_positive_rate", src)
        self.assertIn("model_rankings", src)
        self.assertIn("score_margin", src)


if __name__ == "__main__":
    unittest.main()
