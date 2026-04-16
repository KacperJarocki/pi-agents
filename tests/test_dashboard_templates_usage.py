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
        self.assertIn('Inference Trail', device_template)
        self.assertIn('Retention: 7 days', device_template)
        self.assertIn('chart-shell', device_template)
        self.assertIn('traffic-meta', device_template)
        self.assertIn('inference-meta', device_template)
        self.assertIn('Behavior Alerts', device_template)
        self.assertIn('Risk Contributors', device_template)


if __name__ == "__main__":
    unittest.main()
