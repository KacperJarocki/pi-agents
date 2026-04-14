import ipaddress
import subprocess

from .models import WifiConfig, ValidationResult


def _iface_exists(name: str) -> bool:
    try:
        subprocess.run(["ip", "link", "show", name], check=True, capture_output=True)
        return True
    except Exception:
        return False


def _bin_exists(name: str) -> bool:
    try:
        subprocess.run(["sh", "-c", f"command -v {name}"], check=True, capture_output=True)
        return True
    except Exception:
        return False


def validate_config(cfg: WifiConfig) -> ValidationResult:
    issues: list[str] = []

    if cfg.ap_interface == cfg.upstream_interface:
        issues.append("ap_interface must differ from upstream_interface")

    if not _iface_exists(cfg.ap_interface):
        issues.append(f"ap_interface {cfg.ap_interface} not found")
    if not _iface_exists(cfg.upstream_interface):
        issues.append(f"upstream_interface {cfg.upstream_interface} not found")

    try:
        net = ipaddress.ip_network(cfg.subnet_cidr, strict=True)
    except Exception as e:
        issues.append(f"invalid subnet_cidr: {e}")
        return ValidationResult(ok=False, issues=issues)

    try:
        gw = ipaddress.ip_address(cfg.gateway_ip)
        if gw not in net:
            issues.append("gateway_ip not in subnet")
    except Exception as e:
        issues.append(f"invalid gateway_ip: {e}")

    try:
        start = ipaddress.ip_address(cfg.dhcp_range_start)
        end = ipaddress.ip_address(cfg.dhcp_range_end)
        if start not in net or end not in net:
            issues.append("dhcp range not in subnet")
        if int(start) > int(end):
            issues.append("dhcp_range_start must be <= dhcp_range_end")
        if "gw" in locals() and start <= gw <= end:
            issues.append("gateway_ip must not be inside dhcp range")
    except Exception as e:
        issues.append(f"invalid dhcp range: {e}")

    # Hard safety rails for a k8s node: don't ever touch eth0 except as upstream.
    if cfg.ap_interface == "eth0":
        issues.append("refusing to use eth0 as AP interface")

    if not _bin_exists("hostapd"):
        issues.append("hostapd not installed")
    if not _bin_exists("dnsmasq"):
        issues.append("dnsmasq not installed")
    if not _bin_exists("iptables"):
        issues.append("iptables not installed")

    return ValidationResult(ok=len(issues) == 0, issues=issues)
