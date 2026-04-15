import unittest


class TestK8sIngressFiles(unittest.TestCase):
    def test_gateway_uses_standard_ingress(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[1]
        kustom = (repo / "k8s" / "gateway" / "kustomization.yaml").read_text()

        self.assertIn("gateway-api-ingress.yaml", kustom)
        self.assertIn("dashboard-ingress.yaml", kustom)
        self.assertNotIn("IngressRoute", kustom)

        api_ing = (repo / "k8s" / "gateway" / "gateway-api-ingress.yaml").read_text()
        dash_ing = (repo / "k8s" / "gateway" / "dashboard-ingress.yaml").read_text()

        self.assertIn("kind: Ingress", api_ing)
        self.assertIn("ingressClassName: traefik", api_ing)
        self.assertIn("kind: Ingress", dash_ing)
        self.assertIn("ingressClassName: traefik", dash_ing)


if __name__ == "__main__":
    unittest.main()
