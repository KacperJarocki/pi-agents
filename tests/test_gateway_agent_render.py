import unittest


class TestGatewayAgentRender(unittest.TestCase):
    def test_render_contains_expected_fields(self):
        import sys
        from pathlib import Path

        root = str(Path(__file__).resolve().parents[1] / "images" / "gateway-agent")
        if root not in sys.path:
            sys.path.insert(0, root)

        for k in list(sys.modules.keys()):
            if k == "app" or k.startswith("app."):
                del sys.modules[k]

        from app.models import WifiConfig
        from app.render import render_hostapd, render_dnsmasq

        cfg = WifiConfig(ssid="IoT-Security", psk="supersecretpassword")
        h = render_hostapd(cfg)
        d = render_dnsmasq(cfg)

        self.assertIn("interface=wlan0", h)
        self.assertIn("ssid=IoT-Security", h)
        self.assertIn("wpa_passphrase=supersecretpassword", h)

        self.assertIn("interface=wlan0", d)
        self.assertIn("dhcp-range=192.168.50.100,192.168.50.200", d)
        self.assertIn("dhcp-option=option:router,192.168.50.1", d)


if __name__ == "__main__":
    unittest.main()
