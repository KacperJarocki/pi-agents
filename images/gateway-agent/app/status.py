import subprocess


def _iface_exists(name: str) -> bool:
    try:
        subprocess.run(["ip", "link", "show", name], check=True, capture_output=True)
        return True
    except Exception:
        return False


def _get_iface_ip(name: str) -> str | None:
    try:
        out = subprocess.run(["ip", "-4", "addr", "show", name], check=True, capture_output=True, text=True).stdout
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                return line.split()[1]
    except Exception:
        return None
    return None


def _ip_forward_enabled() -> bool | None:
    try:
        out = subprocess.run(["sysctl", "-n", "net.ipv4.ip_forward"], check=True, capture_output=True, text=True).stdout.strip()
        return out == "1"
    except Exception:
        return None


def _nat_rule_present() -> bool | None:
    # We will create our own chain later; for now we just report whether any MASQUERADE exists.
    try:
        out = subprocess.run(["iptables", "-t", "nat", "-S"], check=True, capture_output=True, text=True).stdout
        return "MASQUERADE" in out
    except Exception:
        return None


def get_status(ap_interface: str = "wlan0", upstream_interface: str = "eth0") -> dict:
    return {
        "ap_interface_exists": _iface_exists(ap_interface),
        "upstream_interface_exists": _iface_exists(upstream_interface),
        "ap_ip": _get_iface_ip(ap_interface),
        "ip_forward": _ip_forward_enabled(),
        "nat_rule_present": _nat_rule_present(),
    }
