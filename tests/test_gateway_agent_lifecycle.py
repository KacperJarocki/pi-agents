import unittest


class TestGatewayAgentLifecycle(unittest.TestCase):
    def test_lifespan_has_shutdown_cleanup(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "images" / "gateway-agent" / "app" / "main.py").read_text()

        self.assertIn("finally:", src)
        self.assertIn("await runtime.stop()", src)

    def test_gateway_deployment_has_graceful_shutdown_settings(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        src = (repo / "k8s" / "gateway" / "gateway-agent-deployment.yaml").read_text()

        self.assertIn("terminationGracePeriodSeconds: 20", src)
        self.assertIn("preStop:", src)


if __name__ == "__main__":
    unittest.main()
