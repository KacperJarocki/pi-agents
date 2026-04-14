from __future__ import annotations

import subprocess


NAT_TABLE = "nat"
FILTER_TABLE = "filter"

NAT_CHAIN = "IOT_GATEWAY_NAT"
FWD_CHAIN = "IOT_GATEWAY_FWD"


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


def ensure_jump_rules():
    # Jump from POSTROUTING and FORWARD into our chains.
    if not _exists(["iptables", "-t", NAT_TABLE, "-C", "POSTROUTING", "-j", NAT_CHAIN]):
        _run(["iptables", "-t", NAT_TABLE, "-A", "POSTROUTING", "-j", NAT_CHAIN])

    if not _exists(["iptables", "-t", FILTER_TABLE, "-C", "FORWARD", "-j", FWD_CHAIN]):
        _run(["iptables", "-t", FILTER_TABLE, "-A", "FORWARD", "-j", FWD_CHAIN])


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
    subprocess.run(["iptables", "-t", NAT_TABLE, "-F", NAT_CHAIN], check=False)
    subprocess.run(["iptables", "-t", FILTER_TABLE, "-F", FWD_CHAIN], check=False)
    subprocess.run(["iptables", "-t", NAT_TABLE, "-X", NAT_CHAIN], check=False)
    subprocess.run(["iptables", "-t", FILTER_TABLE, "-X", FWD_CHAIN], check=False)
