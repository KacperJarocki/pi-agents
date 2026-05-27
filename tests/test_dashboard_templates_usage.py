import unittest


class TestDashboardTemplateUsage(unittest.TestCase):
    def test_template_response_uses_request_first(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "dashboard" / "app" / "main.py").read_text()
        device_template = (repo / "images" / "dashboard" / "app" / "templates" / "device.html").read_text()

        self.assertIn('TemplateResponse(\n        request,\n        "index.html"', src)
        self.assertIn('TemplateResponse(\n        request,\n        "gateway.html"', src)
        self.assertIn('TemplateResponse(\n        request,\n        "device.html"', src)
        self.assertIn('Traffic Profile', device_template)
        self.assertIn('Risk Score Timeline', device_template)
        self.assertIn('Retention: 7 days', device_template)
        self.assertIn('chart-shell', device_template)
        self.assertIn('traffic-meta', device_template)
        self.assertIn('risk-timeline-chart', device_template)
        self.assertIn('Behavior Alerts', device_template)
        self.assertIn('Risk Contributors', device_template)
        self.assertIn('Risk Breakdown', device_template)
        self.assertIn('Behavior Baseline', device_template)
        self.assertIn('Protocol Signals', device_template)
        self.assertIn('/api/devices/${deviceId}/protocol-signals?hours=24', device_template)
        self.assertIn("risk-status", device_template)
        self.assertIn("risk-top-reason", device_template)
        self.assertIn("correlation_bonus", device_template)
        self.assertIn("Model Versions", device_template)
        self.assertIn("model-versions-body", device_template)
        self.assertIn("loadModelVersions", device_template)
        self.assertIn("activateModelVersion", device_template)
        self.assertIn("Historical Model Replay", device_template)
        self.assertIn("model-replay-chart", device_template)
        self.assertIn("loadHistoricalModelReplay", device_template)
        self.assertIn("All models", device_template)
        self.assertIn("replay-score-metric", device_template)
        self.assertIn("anomaly_score", device_template)
        self.assertIn("risk_score", device_template)
        self.assertIn("--compare-all", device_template)
        self.assertIn("model-replay", device_template)
        self.assertIn("replayModelVersion", device_template)
        self.assertIn("modelRegistryId", device_template)
        self.assertIn("redrawHistoricalModelReplay", device_template)
        self.assertIn("_lastModelReplay", device_template)
        self.assertIn("animation: false", device_template)
        self.assertIn("model-backtest.sh", device_template)
        self.assertIn("parseApiTs", device_template)
        self.assertIn("fmtAxisDateTime", device_template)
        self.assertIn("fmtDateTime", device_template)
        self.assertIn("height:460px", device_template)
        self.assertIn("model-scores?model_type=${mt}&hours=24", device_template)
        self.assertIn("model-comparison?hours=168", device_template)
        self.assertIn("primary", device_template)
        self.assertIn("shadow", device_template)
        self.assertIn("score_margin", device_template)
        self.assertIn("maxTicksLimit: 12", device_template)
        self.assertIn("tooltip", device_template)
        self.assertIn("parseApiTs", (repo / "images" / "dashboard" / "app" / "templates" / "index.html").read_text())

    def test_dashboard_proxies_model_versions(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "dashboard" / "app" / "main.py").read_text()

        self.assertIn('/api/devices/{device_id}/model-versions', src)
        self.assertIn('/model-versions/{version_id}/activate', src)
        self.assertIn('/api/devices/{device_id}/model-replay', src)
        self.assertIn('/api/devices/{device_id}/model-comparison', src)


if __name__ == "__main__":
    unittest.main()
