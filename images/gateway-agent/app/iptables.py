from __future__ import annotations

import re
import subprocess


NAT_TABLE = "nat"
FILTER_TABLE = "filter"

NAT_CHAIN = "IOT_GATEWAY_NAT"
FWD_CHAIN = "IOT_GATEWAY_FWD"
BLOCK_CHAIN = "IOT_DEVICE_BLOCK"

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _exists(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except Exception:
        return False


def ensure_chains():
    # Create chains if missing.
    if not _exists(["iptables", "-t", NAT_TABLE, "-S", NAT_CHAIN]):
        subprocess.run(["iptables", "-t", NAT_TABLE, "-N", NAT_CHAIN], check=False)
    if not _exists(["iptables", "-t", FILTER_TABLE, "-S", FWD_CHAIN]):
        subprocess.run(["iptables", "-t", FILTER_TABLE, "-N", FWD_CHAIN], check=False)
    if not _exists(["iptables", "-t", FILTER_TABLE, "-S", BLOCK_CHAIN]):
        subprocess.run(["iptables", "-t", FILTER_TABLE, "-N", BLOCK_CHAIN], check=False)


def ensure_jump_rules():
    # Jump from POSTROUTING and FORWARD into our chains.
    if not _exists(["iptables", "-t", NAT_TABLE, "-C", "POSTROUTING", "-j", NAT_CHAIN]):
        _run(["iptables", "-t", NAT_TABLE, "-A", "POSTROUTING", "-j", NAT_CHAIN])

    if not _exists(["iptables", "-t", FILTER_TABLE, "-C", "FORWARD", "-j", FWD_CHAIN]):
        _run(["iptables", "-t", FILTER_TABLE, "-A", "FORWARD", "-j", FWD_CHAIN])

    # Block chain must be checked BEFORE the forwarding accept rules.
    if not _exists(["iptables", "-t", FILTER_TABLE, "-C", "FORWARD", "-j", BLOCK_CHAIN]):
        _run(["iptables", "-t", FILTER_TABLE, "-I", "FORWARD", "1", "-j", BLOCK_CHAIN])


def ensure_nat_rules(subnet_cidr: str, ap_interface: str, upstream_interface: str):
    # NAT
    if not _exists(
        [
            "iptables",
            "-t",
            NAT_TABLE,
            "-C",
            NAT_CHAIN,
            "-s",
            subnet_cidr,
            "-o",
            upstream_interface,
            "-j",
            "MASQUERADE",
        ]
    ):
        _run(
            [
                "iptables",
                "-t",
                NAT_TABLE,
                "-A",
                NAT_CHAIN,
                "-s",
                subnet_cidr,
                "-o",
                upstream_interface,
                "-j",
                "MASQUERADE",
            ]
        )

    # Forwarding rules
    if not _exists(
        [
            "iptables",
            "-t",
            FILTER_TABLE,
            "-C",
            FWD_CHAIN,
            "-i",
            ap_interface,
            "-o",
            upstream_interface,
            "-j",
            "ACCEPT",
        ]
    ):
        _run(
            [
                "iptables",
                "-t",
                FILTER_TABLE,
                "-A",
                FWD_CHAIN,
                "-i",
                ap_interface,
                "-o",
                upstream_interface,
                "-j",
                "ACCEPT",
            ]
        )

    if not _exists(
        [
            "iptables",
            "-t",
            FILTER_TABLE,
            "-C",
            FWD_CHAIN,
            "-i",
            upstream_interface,
            "-o",
            ap_interface,
            "-m",
            "conntrack",
            "--ctstate",
            "RELATED,ESTABLISHED",
            "-j",
            "ACCEPT",
        ]
    ):
        _run(
            [
                "iptables",
                "-t",
                FILTER_TABLE,
                "-A",
                FWD_CHAIN,
                "-i",
                upstream_interface,
                "-o",
                ap_interface,
                "-m",
                "conntrack",
                "--ctstate",
                "RELATED,ESTABLISHED",
                "-j",
                "ACCEPT",
            ]
        )


def teardown():
    # Remove jump rules, then flush and delete chains.
    subprocess.run(["iptables", "-t", NAT_TABLE, "-D", "POSTROUTING", "-j", NAT_CHAIN], check=False)
    subprocess.run(["iptables", "-t", FILTER_TABLE, "-D", "FORWARD", "-j", FWD_CHAIN], check=False)
    subprocess.run(["iptables", "-t", FILTER_TABLE, "-D", "FORWARD", "-j", BLOCK_CHAIN], check=False)
    subprocess.run(["iptables", "-t", NAT_TABLE, "-F", NAT_CHAIN], check=False)
    subprocess.run(["iptables", "-t", FILTER_TABLE, "-F", FWD_CHAIN], check=False)
    subprocess.run(["iptables", "-t", FILTER_TABLE, "-F", BLOCK_CHAIN], check=False)
    subprocess.run(["iptables", "-t", NAT_TABLE, "-X", NAT_CHAIN], check=False)
    subprocess.run(["iptables", "-t", FILTER_TABLE, "-X", FWD_CHAIN], check=False)
    subprocess.run(["iptables", "-t", FILTER_TABLE, "-X", BLOCK_CHAIN], check=False)


def _validate_mac(mac: str) -> str:
    mac = mac.strip().lower()
    if not _MAC_RE.match(mac):
        raise ValueError(f"Invalid MAC address: {mac}")
    return mac


def block_device(mac: str) -> bool:
    """Add a DROP rule for the given MAC. Returns True if added, False if already present."""
    mac = _validate_mac(mac)
    if _exists([
        "iptables", "-t", FILTER_TABLE, "-C", BLOCK_CHAIN,
        "-m", "mac", "--mac-source", mac, "-j", "DROP",
    ]):
        return False
    _run([
        "iptables", "-t", FILTER_TABLE, "-A", BLOCK_CHAIN,
        "-m", "mac", "--mac-source", mac, "-j", "DROP",
    ])
    return True


def unblock_device(mac: str) -> bool:
    """Remove the DROP rule for the given MAC. Returns True if removed, False if not found."""
    mac = _validate_mac(mac)
    if not _exists([
        "iptables", "-t", FILTER_TABLE, "-C", BLOCK_CHAIN,
        "-m", "mac", "--mac-source", mac, "-j", "DROP",
    ]):
        return False
    _run([
        "iptables", "-t", FILTER_TABLE, "-D", BLOCK_CHAIN,
        "-m", "mac", "--mac-source", mac, "-j", "DROP",
    ])
    return True


def list_blocked() -> list[str]:
    """Return list of blocked MAC addresses."""
    try:
        result = _run(["iptables", "-t", FILTER_TABLE, "-S", BLOCK_CHAIN])
    except subprocess.CalledProcessError:
        return []
    macs = []
    for line in result.stdout.splitlines():
        # Lines look like: -A IOT_DEVICE_BLOCK -m mac --mac-source aa:bb:cc:dd:ee:ff -j DROP
        if "--mac-source" in line and "-j DROP" in line:
            parts = line.split()
            try:
                idx = parts.index("--mac-source")
                macs.append(parts[idx + 1].lower())
            except (ValueError, IndexError):
                pass
    return macs
