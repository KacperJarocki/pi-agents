import unittest


class TestInferenceStatusSources(unittest.TestCase):
    def test_device_schema_exposes_last_inference_fields(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-api" / "app" / "models" / "schemas_pydantic.py").read_text()

        self.assertIn("last_inference_score: Optional[float] = None", src)
        self.assertIn("last_inference_at: Optional[datetime] = None", src)

    def test_inference_updates_risk_and_last_score(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "ml-pipeline" / "app" / "inference.py").read_text()

        self.assertIn("inference_device_score", src)
        self.assertIn("last_inference_score=float(score)", src)
        self.assertIn("update_device_risk_score(", src)

    def test_ml_core_updates_last_inference_columns(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "ml-pipeline" / "app" / "ml_core.py").read_text()

        self.assertIn("_ensure_device_inference_columns", src)
        self.assertIn("last_inference_score", src)
        self.assertIn("last_inference_at = CURRENT_TIMESTAMP", src)
        self.assertIn("def score(self, features: pd.DataFrame)", src)

    def test_behavior_alert_schema_is_exposed(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-api" / "app" / "models" / "schemas_pydantic.py").read_text()

        self.assertIn("class DeviceBehaviorAlertResponse(BaseModel):", src)
        self.assertIn("class DeviceRiskContributorsResponse(BaseModel):", src)
        self.assertIn("class DeviceBehaviorBaselineResponse(BaseModel):", src)
        self.assertIn("category: str", src)
        self.assertIn("effective_score: float", src)
        self.assertIn("correlation_bonus: float = 0.0", src)
        self.assertIn("top_reason: str", src)

    def test_behavior_alert_service_normalizes_resolved_and_evidence(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-api" / "app" / "services" / "crud.py").read_text()

        self.assertIn("def _normalize_alert(self, alert: DeviceBehaviorAlert)", src)
        self.assertIn("alert.resolved = bool(alert.resolved)", src)
        self.assertIn("json.loads(alert.evidence)", src)

    def test_risk_contributors_apply_decay_and_deduplication(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-api" / "app" / "services" / "crud.py").read_text()

        self.assertIn("def _decay_multiplier(self, timestamp: datetime, now: datetime)", src)
        self.assertIn("contributors_by_type", src)
        self.assertIn("effective_score", src)
        self.assertIn("return sorted(contributors_by_type.values()", src)


if __name__ == "__main__":
    unittest.main()
