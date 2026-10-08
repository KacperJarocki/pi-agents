import unittest


class TestPhase1PresenceSources(unittest.TestCase):
    def test_gateway_agent_status_supports_connected_clients(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        models_src = (repo / "images" / "gateway-agent" / "app" / "models.py").read_text()
        main_src = (repo / "images" / "gateway-agent" / "app" / "main.py").read_text()

        self.assertIn("connected_clients: list[dict] | None = None", models_src)
        self.assertIn('"connected_clients": clients', main_src)
        self.assertIn('"lease_count": len(clients)', main_src)


if __name__ == "__main__":
    unittest.main()
