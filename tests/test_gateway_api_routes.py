import unittest


class TestGatewayApiRoutes(unittest.TestCase):
    def test_gateway_wifi_routes_exist(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        router_file = repo / "images" / "gateway-api" / "app" / "routers" / "gateway_wifi.py"
        main_file = repo / "images" / "gateway-api" / "app" / "main.py"
        routers_init = repo / "images" / "gateway-api" / "app" / "routers" / "__init__.py"

        router_src = router_file.read_text()
        self.assertIn('APIRouter(prefix="/gateway/wifi"', router_src)
        self.assertIn('@router.get("/config"', router_src)
        self.assertIn('@router.put("/config"', router_src)
        self.assertIn('@router.post("/validate"', router_src)
        self.assertIn('@router.post("/apply"', router_src)
        self.assertIn('@router.post("/rollback"', router_src)
        self.assertIn('@router.get("/status"', router_src)

        self.assertIn("gateway_wifi_router", routers_init.read_text())
        self.assertIn("app.include_router(gateway_wifi_router", main_file.read_text())

    def test_device_detail_routes_exist(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        router_src = (repo / "images" / "gateway-api" / "app" / "routers" / "devices.py").read_text()

        self.assertIn('@router.get("/{device_id}/traffic"', router_src)
        self.assertIn('@router.get("/{device_id}/destinations"', router_src)
        self.assertIn('@router.get("/{device_id}/anomalies"', router_src)
        self.assertIn('@router.get("/{device_id}/inference-history"', router_src)
        self.assertIn('@router.get("/{device_id}/behavior-alerts"', router_src)
        self.assertIn('@router.get("/{device_id}/risk-contributors"', router_src)
        self.assertIn('@router.get("/{device_id}/behavior-baseline"', router_src)
        self.assertIn('@router.get("/{device_id}/protocol-signals"', router_src)
        self.assertIn('@router.get("/{device_id}/model-versions"', router_src)
        self.assertIn('@router.post("/{device_id}/model-versions/{version_id}/activate"', router_src)
        self.assertIn('@router.get("/{device_id}/model-replay"', router_src)
        self.assertIn('@router.get("/{device_id}/model-comparison"', router_src)
        self.assertIn("get_model_comparison", router_src)
        self.assertIn('pattern=r"^(all|isolation_forest|lof|ocsvm|autoencoder)$"', router_src)
        self.assertIn('risk_delta', router_src)
        self.assertIn('correlation_bonus', router_src)
        self.assertIn('latest_device_history_points', router_src)

    def test_model_version_services_exist(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-api" / "app" / "services" / "crud.py").read_text()
        replay = (repo / "images" / "gateway-api" / "app" / "services" / "model_replay.py").read_text()

        self.assertIn("list_model_versions", src)
        self.assertIn("activate_model_version", src)
        self.assertIn("shutil.copy2", src)
        self.assertIn("model_registry", src)
        self.assertIn("ModelReplayService", replay)
        self.assertIn('model_type == "all"', replay)
        self.assertIn("_ARTIFACT_CACHE", replay)
        self.assertIn("os.path.getmtime", replay)
        self.assertIn("traffic_flows", replay)
        self.assertIn("joblib.load", replay)
        self.assertIn("risk_score", replay)
        self.assertIn("_calibrated_ml_risk", replay)
        self.assertIn("anomaly_confidence", replay)
        self.assertIn("dst_port_entropy", replay)
        self.assertIn("risky_port_ratio", replay)

    def test_model_scores_include_research_fields(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-api" / "app" / "services" / "crud.py").read_text()

        for field in ("norm_score", "norm_threshold", "score_margin", "would_alert", "decision_role"):
            self.assertIn(field, src)

    def test_train_now_passes_effective_training_config_to_job(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-api" / "app" / "routers" / "training.py").read_text()

        self.assertIn("training_config=training_config", src)
        self.assertIn("TRAINING_HOURS", src)
        self.assertIn("MIN_TRAINING_SAMPLES", src)
        self.assertIn("FEATURE_BUCKET_MINUTES", src)
        self.assertIn("MODEL_REGISTRY_RETENTION_DAYS", src)


if __name__ == "__main__":
    unittest.main()
