import unittest
from unittest.mock import patch


class TestGatewayAgentValidate(unittest.TestCase):
    def test_validate_ok(self):
        # Import via module path to avoid packaging work.
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "images" / "gateway-agent"))

        from app.models import WifiConfig
        from app.validate import validate_config

        cfg = WifiConfig(
            ssid="IoT-Security",
            psk="supersecretpassword",
            country_code="PL",
            channel=6,
            ap_interface="wlan0",
            upstream_interface="eth0",
            subnet_cidr="192.168.50.0/24",
            gateway_ip="192.168.50.1",
            dhcp_range_start="192.168.50.100",
            dhcp_range_end="192.168.50.200",
            enabled=True,
        )

        with patch("app.validate._iface_exists", return_value=True), patch(
            "app.validate._bin_exists", return_value=True
        ):
            res = validate_config(cfg)

        self.assertTrue(res.ok)
        self.assertEqual(res.issues, [])

    def test_reject_eth0_as_ap(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "images" / "gateway-agent"))

        from app.models import WifiConfig
        from app.validate import validate_config

        cfg = WifiConfig(
            ssid="x",
            psk="supersecretpassword",
            ap_interface="eth0",
            upstream_interface="eth0",
        )

        with patch("app.validate._iface_exists", return_value=True), patch(
            "app.validate._bin_exists", return_value=True
        ):
            res = validate_config(cfg)

        self.assertFalse(res.ok)
        self.assertTrue(any("eth0" in i for i in res.issues))


if __name__ == "__main__":
    unittest.main()
