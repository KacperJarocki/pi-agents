"""The gateway's firewall, kept in chains of its own so the node's rules stay untouched.

- `IOT_GATEWAY_NAT` (nat/POSTROUTING): masquerades the Wi-Fi subnet upstream.
- `IOT_GATEWAY_FWD` (filter/FORWARD): lets Wi-Fi clients out and replies back in.
- `IOT_DEVICE_BLOCK` (filter/FORWARD, first): drops blocked devices by MAC.
- `IOT_GATEWAY_ISOLATE` (filter/FORWARD, first): keeps Wi-Fi clients out of
  private networks (the home LAN, pod and service addresses). It sits ahead of
  the cluster's own FORWARD rules, which would otherwise accept traffic to pods.
- `IOT_GATEWAY_IN` (filter/INPUT, first): Wi-Fi clients reach only DHCP, DNS
  and ping on the gateway itself, not this agent, SSH or the kubelet.
- `IOT_GATEWAY_IN6` / `IOT_GATEWAY_FWD6` (ip6tables, best effort): the access
  point hands out no IPv6 addresses, so IPv6 from Wi-Fi clients is dropped
  apart from ICMPv6. Without this the node's link-local address is reachable.
"""

from __future__ import annotations

import re
import subprocess

NAT_TABLE = "nat"
FILTER_TABLE = "filter"

NAT_CHAIN = "IOT_GATEWAY_NAT"
FWD_CHAIN = "IOT_GATEWAY_FWD"
BLOCK_CHAIN = "IOT_DEVICE_BLOCK"
ISOLATE_CHAIN = "IOT_GATEWAY_ISOLATE"
INPUT_CHAIN = "IOT_GATEWAY_IN"
INPUT6_CHAIN = "IOT_GATEWAY_IN6"
FWD6_CHAIN = "IOT_GATEWAY_FWD6"

# Destinations a Wi-Fi client may not be routed to unless explicitly allowed:
# RFC 1918, link-local and carrier-grade NAT space.
PRIVATE_CIDRS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10")

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _exists(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return True
    except Exception:
        return False


def _ensure_chain(table: str, chain: str, binary: str = "iptables") -> None:
    if not _exists([binary, "-t", table, "-S", chain]):
        subprocess.run([binary, "-t", table, "-N", chain], check=False)


def _ensure_rule(table: str, chain: str, rule: list[str], binary: str = "iptables") -> None:
    if not _exists([binary, "-t", table, "-C", chain, *rule]):
        _run([binary, "-t", table, "-A", chain, *rule])


def _ensure_first(table: str, parent: str, chain: str, binary: str = "iptables") -> None:
    """Jumps to `chain` from the top of `parent`, ahead of rules other software added."""
    if not _exists([binary, "-t", table, "-C", parent, "-j", chain]):
        _run([binary, "-t", table, "-I", parent, "1", "-j", chain])


def _remove_chain(table: str, parent: str, chain: str, binary: str = "iptables") -> None:
    subprocess.run([binary, "-t", table, "-D", parent, "-j", chain], check=False, capture_output=True)
    subprocess.run([binary, "-t", table, "-F", chain], check=False, capture_output=True)
    subprocess.run([binary, "-t", table, "-X", chain], check=False, capture_output=True)


def ensure_chains():
    _ensure_chain(NAT_TABLE, NAT_CHAIN)
    _ensure_chain(FILTER_TABLE, FWD_CHAIN)
    _ensure_chain(FILTER_TABLE, BLOCK_CHAIN)


def ensure_jump_rules():
    # Jump from POSTROUTING and FORWARD into our chains.
    if not _exists(["iptables", "-t", NAT_TABLE, "-C", "POSTROUTING", "-j", NAT_CHAIN]):
        _run(["iptables", "-t", NAT_TABLE, "-A", "POSTROUTING", "-j", NAT_CHAIN])

    if not _exists(["iptables", "-t", FILTER_TABLE, "-C", "FORWARD", "-j", FWD_CHAIN]):
        _run(["iptables", "-t", FILTER_TABLE, "-A", "FORWARD", "-j", FWD_CHAIN])

    # Block chain must be checked BEFORE the forwarding accept rules.
    _ensure_first(FILTER_TABLE, "FORWARD", BLOCK_CHAIN)


def ensure_nat_rules(subnet_cidr: str, ap_interface: str, upstream_interface: str):
    _ensure_rule(NAT_TABLE, NAT_CHAIN, ["-s", subnet_cidr, "-o", upstream_interface, "-j", "MASQUERADE"])
    _ensure_rule(FILTER_TABLE, FWD_CHAIN, ["-i", ap_interface, "-o", upstream_interface, "-j", "ACCEPT"])
    _ensure_rule(
        FILTER_TABLE,
        FWD_CHAIN,
        [
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
        ],
    )


def input_rules(ap_interface: str) -> list[list[str]]:
    """What a Wi-Fi client may send to the gateway itself; everything else is refused."""
    ap = ["-i", ap_interface]
    return [
        [*ap, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "RETURN"],
        [*ap, "-p", "udp", "--dport", "67", "-j", "RETURN"],  # DHCP
        [*ap, "-p", "udp", "--dport", "53", "-j", "RETURN"],  # DNS
        [*ap, "-p", "tcp", "--dport", "53", "-j", "RETURN"],
        [*ap, "-p", "icmp", "-j", "RETURN"],
        # Refuse like a closed port, so clients fail fast instead of timing out.
        [*ap, "-p", "tcp", "-j", "REJECT", "--reject-with", "tcp-reset"],
        [*ap, "-j", "REJECT"],
    ]


def isolation_rules(ap_interface: str, subnet_cidr: str, allowed_cidrs: list[str]) -> list[list[str]]:
    """Wi-Fi clients keep their own subnet and the allowed networks; other private ranges are refused."""
    ap = ["-i", ap_interface]
    rules = [[*ap, "-d", cidr, "-j", "RETURN"] for cidr in (subnet_cidr, *allowed_cidrs)]
    rules += [[*ap, "-d", cidr, "-j", "REJECT"] for cidr in PRIVATE_CIDRS]
    return rules


def _fill(table: str, chain: str, rules: list[list[str]], binary: str = "iptables") -> None:
    """Replaces the chain's rules; the chain is ours, so a flush loses nothing else."""
    _ensure_chain(table, chain, binary)
    _run([binary, "-t", table, "-F", chain])
    for rule in rules:
        _run([binary, "-t", table, "-A", chain, *rule])


def ensure_client_isolation(
    ap_interface: str, subnet_cidr: str, isolate_from_lan: bool, allowed_cidrs: list[str]
) -> str | None:
    """Limits what Wi-Fi clients reach on the gateway and, when asked, in private networks.

    Returns a warning when the IPv6 rules could not be installed (IPv4 rules
    raise instead: without them the gateway must not come up half-protected).
    """
    _fill(FILTER_TABLE, INPUT_CHAIN, input_rules(ap_interface))
    _ensure_first(FILTER_TABLE, "INPUT", INPUT_CHAIN)

    if isolate_from_lan:
        _fill(FILTER_TABLE, ISOLATE_CHAIN, isolation_rules(ap_interface, subnet_cidr, allowed_cidrs))
        _ensure_first(FILTER_TABLE, "FORWARD", ISOLATE_CHAIN)
    else:
        _remove_chain(FILTER_TABLE, "FORWARD", ISOLATE_CHAIN)

    try:
        ap = ["-i", ap_interface]
        _fill(
            FILTER_TABLE,
            INPUT6_CHAIN,
            [
                [*ap, "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "RETURN"],
                [*ap, "-p", "ipv6-icmp", "-j", "RETURN"],
                [*ap, "-j", "DROP"],
            ],
            binary="ip6tables",
        )
        _ensure_first(FILTER_TABLE, "INPUT", INPUT6_CHAIN, binary="ip6tables")
        _fill(FILTER_TABLE, FWD6_CHAIN, [[*ap, "-j", "DROP"]], binary="ip6tables")
        _ensure_first(FILTER_TABLE, "FORWARD", FWD6_CHAIN, binary="ip6tables")
    except (subprocess.CalledProcessError, OSError) as err:
        detail = getattr(err, "stderr", "") or str(err)
        return f"IPv6 client isolation not installed: {detail.strip()}"
    return None


def teardown():
    # Remove jump rules, then flush and delete chains.
    _remove_chain(NAT_TABLE, "POSTROUTING", NAT_CHAIN)
    _remove_chain(FILTER_TABLE, "FORWARD", FWD_CHAIN)
    _remove_chain(FILTER_TABLE, "FORWARD", BLOCK_CHAIN)
    _remove_chain(FILTER_TABLE, "FORWARD", ISOLATE_CHAIN)
    _remove_chain(FILTER_TABLE, "INPUT", INPUT_CHAIN)
    _remove_chain(FILTER_TABLE, "INPUT", INPUT6_CHAIN, binary="ip6tables")
    _remove_chain(FILTER_TABLE, "FORWARD", FWD6_CHAIN, binary="ip6tables")


def _validate_mac(mac: str) -> str:
    mac = mac.strip().lower()
    if not _MAC_RE.match(mac):
        raise ValueError(f"Invalid MAC address: {mac}")
    return mac


def _block_rule(mac: str) -> list[str]:
    return ["-m", "mac", "--mac-source", mac, "-j", "DROP"]


def block_device(mac: str) -> bool:
    """Add a DROP rule for the given MAC. Returns True if added, False if already present."""
    mac = _validate_mac(mac)
    if _exists(["iptables", "-t", FILTER_TABLE, "-C", BLOCK_CHAIN, *_block_rule(mac)]):
        return False
    _run(["iptables", "-t", FILTER_TABLE, "-A", BLOCK_CHAIN, *_block_rule(mac)])
    return True


def unblock_device(mac: str) -> bool:
    """Remove the DROP rule for the given MAC. Returns True if removed, False if not found."""
    mac = _validate_mac(mac)
    if not _exists(["iptables", "-t", FILTER_TABLE, "-C", BLOCK_CHAIN, *_block_rule(mac)]):
        return False
    _run(["iptables", "-t", FILTER_TABLE, "-D", BLOCK_CHAIN, *_block_rule(mac)])
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
