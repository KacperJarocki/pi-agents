import unittest


class TestMlMvpSources(unittest.TestCase):
    def test_feature_extractor_uses_bucketed_samples(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "ml-pipeline" / "app" / "ml_core.py").read_text()

        self.assertIn("FEATURE_BUCKET_MINUTES", src)
        self.assertIn("bucket_start", src)
        self.assertIn("groupby(['device_id', 'bucket_start'])", src)

    def test_training_uses_per_device_models(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "ml-pipeline" / "app" / "train.py").read_text()

        self.assertIn('PER_DEVICE_MODELS', src)
        self.assertIn("training_complete_for_device", src)
        self.assertIn("save_model(model, device_id=int(device_id))", src)

    def test_inference_uses_latest_bucket_per_device(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "ml-pipeline" / "app" / "inference.py").read_text()

        self.assertIn("inference_model_missing_for_device", src)
        self.assertIn("tail(1)", src)
        self.assertIn("load_model(device_id=int(device_id))", src)

    def test_k8s_ml_manifests_set_mvp_env(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        trainer = (repo / "k8s" / "gateway" / "ml-trainer-cronjob.yaml").read_text()
        inference = (repo / "k8s" / "gateway" / "ml-inference-deployment.yaml").read_text()

        self.assertIn('MIN_TRAINING_SAMPLES', trainer)
        self.assertIn('FEATURE_BUCKET_MINUTES', trainer)
        self.assertIn('PER_DEVICE_MODELS', trainer)
        self.assertIn('value: "2"', trainer)
        self.assertIn('FEATURE_BUCKET_MINUTES', inference)
        self.assertIn('INFERENCE_INTERVAL', inference)
        self.assertIn('BEHAVIOR_BASELINE_HOURS', inference)
        self.assertIn('PER_DEVICE_MODELS', inference)

    def test_metrics_router_exposes_ml_status(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-api" / "app" / "routers" / "metrics.py").read_text()

        self.assertIn('@router.get("/ml-status"', src)

    def test_inference_persists_history_with_retention(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        ml_core = (repo / "images" / "ml-pipeline" / "app" / "ml_core.py").read_text()
        inference = (repo / "images" / "ml-pipeline" / "app" / "inference.py").read_text()

        self.assertIn("device_inference_history", ml_core)
        self.assertIn("retention_days=7", inference)
        self.assertIn("save_inference_result(", inference)

    def test_inference_risk_uses_threshold_scaling(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        inference = (repo / "images" / "ml-pipeline" / "app" / "inference.py").read_text()

        self.assertIn("def _risk_from_score(score: float, threshold: float)", inference)
        self.assertIn('"threshold": device_detector.threshold', inference)
        self.assertIn("threshold=threshold", inference)

    def test_inference_builds_behavior_alerts(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        inference = (repo / "images" / "ml-pipeline" / "app" / "inference.py").read_text()
        ml_core = (repo / "images" / "ml-pipeline" / "app" / "ml_core.py").read_text()

        self.assertIn("def _build_behavior_alerts(", inference)
        self.assertIn("destination_novelty", inference)
        self.assertIn("dns_burst", inference)
        self.assertIn("port_churn", inference)
        self.assertIn("traffic_pattern_drift", inference)
        self.assertIn("beaconing_suspected", inference)
        self.assertIn("def _baseline_stats(values: list[float])", inference)
        self.assertIn("save_behavior_alert(", inference)
        self.assertIn("device_behavior_alerts", ml_core)


if __name__ == "__main__":
    unittest.main()
