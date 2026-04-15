import unittest


class TestDashboardTemplateUsage(unittest.TestCase):
    def test_template_response_uses_request_first(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "dashboard" / "app" / "main.py").read_text()

        self.assertIn('TemplateResponse(\n        request,\n        "index.html"', src)
        self.assertIn('TemplateResponse(\n        request,\n        "gateway.html"', src)


if __name__ == "__main__":
    unittest.main()
