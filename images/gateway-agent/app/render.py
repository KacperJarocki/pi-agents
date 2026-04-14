from __future__ import annotations

import ipaddress

from .models import WifiConfig


def render_hostapd(cfg: WifiConfig) -> str:
    # Minimal, modern WPA2 config. hostapd will validate the rest.
    return "\n".join(
        [
            f"interface={cfg.ap_interface}",
            "driver=nl80211",
            f"ssid={cfg.ssid}",
            f"country_code={cfg.country_code}",
            f"channel={cfg.channel}",
            # 2.4G by default; for channel > 14 hostapd switches to 5G.
            "hw_mode=g",
            "wmm_enabled=1",
            "auth_algs=1",
            "wpa=2",
            f"wpa_passphrase={cfg.psk}",
            "wpa_key_mgmt=WPA-PSK",
            "rsn_pairwise=CCMP",
            "\n",
        ]
    )


def render_dnsmasq(cfg: WifiConfig) -> str:
    net = ipaddress.ip_network(cfg.subnet_cidr, strict=True)
    # dnsmasq expects netmask, not CIDR
    netmask = net.netmask

    return "\n".join(
        [
            "# Managed by gateway-agent",
            "domain-needed",
            "bogus-priv",
            "no-resolv",
            "server=1.1.1.1",
            "server=8.8.8.8",
            "log-dhcp",
            "log-queries",
            f"interface={cfg.ap_interface}",
            "bind-interfaces",
            f"dhcp-range={cfg.dhcp_range_start},{cfg.dhcp_range_end},{netmask},12h",
            f"dhcp-option=option:router,{cfg.gateway_ip}",
            f"dhcp-option=option:dns-server,{cfg.gateway_ip}",
            "\n",
        ]
    )
