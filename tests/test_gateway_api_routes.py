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


if __name__ == "__main__":
    unittest.main()
