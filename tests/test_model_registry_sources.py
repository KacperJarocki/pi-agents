import unittest
from pathlib import Path


class TestModelRegistrySources(unittest.TestCase):
    def test_model_registry_schema_and_archive_helpers_exist(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "ml-pipeline" / "app" / "ml_core.py").read_text()

        self.assertIn("CREATE TABLE IF NOT EXISTS model_registry", src)
        self.assertIn("MODEL_REGISTRY_RETENTION_DAYS", src)
        self.assertIn("def _archive_model_file", src)
        self.assertIn("save_model_registry_entry", src)
        self.assertIn("artifact_sha256", src)
        self.assertIn("archive", src)

    def test_training_persists_model_registry_entries(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "ml-pipeline" / "app" / "train.py").read_text()

        self.assertIn("save_model_registry_entry", src)
        self.assertIn("trained_at=trained_at", src)

    def test_backtest_script_supports_fp_fn_classification(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "model-backtest.py").read_text()

        self.assertIn("model_registry", src)
        self.assertIn("scores.jsonl", src)
        self.assertIn("classification", src)
        self.assertIn("TP", src)
        self.assertIn("FN", src)
        self.assertIn("FP", src)
        self.assertIn("TN", src)
        self.assertIn("load_model_file", src)

    def test_backtest_wrapper_exists(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "model-backtest.sh").read_text()

        self.assertIn("model-backtest.py", src)
        self.assertIn("$@", src)

    def test_model_activate_script_supports_rollback(self):
        repo = Path(__file__).resolve().parents[1]
        src = (repo / "scripts" / "model-activate.py").read_text()

        self.assertIn("model_registry", src)
        self.assertIn("shutil.copy2", src)
        self.assertIn("active = 1", src)
        self.assertIn("current_model_path", src)


if __name__ == "__main__":
    unittest.main()
