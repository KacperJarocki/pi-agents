import unittest


class TestPhase1PresenceSources(unittest.TestCase):
    def test_device_response_supports_presence_fields(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-api" / "app" / "models" / "schemas_pydantic.py").read_text()

        self.assertIn("connected: bool = False", src)
        self.assertIn("connection_source: Optional[str] = None", src)
        self.assertIn("model_status: Optional[str] = None", src)

    def test_gateway_agent_status_supports_connected_clients(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        models_src = (repo / "images" / "gateway-agent" / "app" / "models.py").read_text()
        main_src = (repo / "images" / "gateway-agent" / "app" / "main.py").read_text()

        self.assertIn("connected_clients: list[dict] | None = None", models_src)
        self.assertIn('"connected_clients": clients', main_src)
        self.assertIn('"lease_count": len(clients)', main_src)

    def test_device_service_supports_synthetic_lease_devices(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-api" / "app" / "services" / "crud.py").read_text()

        self.assertIn("def _synthetic_device(self, client: dict, idx: int)", src)
        self.assertIn('connection_source="dhcp_lease"', src)
        self.assertIn("devices.extend(synthetic)", src)


if __name__ == "__main__":
    unittest.main()
