"""Gateway-agent hardening: client isolation rules, the API token and saved blocks."""

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = str(Path(__file__).resolve().parents[1] / "images" / "gateway-agent")


def _load(name: str):
    """Imports a gateway-agent module (other images also call their package `app`)."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]
    return importlib.import_module(name)


class FakeIptables:
    """Stands in for subprocess.run: remembers rules per (binary, table, chain)."""

    def __init__(self, fail_binary: str | None = None):
        self.chains: dict[tuple[str, str, str], list[list[str]]] = {}
        self.fail_binary = fail_binary

    def run(self, cmd, check=False, **_):
        import subprocess

        binary, _t, table, action, chain, *rest = cmd
        key = (binary, table, chain)
        ok = True
        if binary == self.fail_binary:
            ok = False
        elif action == "-N":
            self.chains.setdefault(key, [])
        elif action == "-S":
            ok = key in self.chains
        elif action == "-F":
            ok = key in self.chains
            self.chains[key] = []
        elif action == "-X":
            ok = self.chains.pop(key, None) is not None
        elif action == "-C":
            ok = rest in self.chains.get(key, [])
        elif action == "-A":
            self.chains.setdefault(key, []).append(rest)
        elif action == "-I":
            self.chains.setdefault(key, []).insert(0, rest[1:])  # drop the position
        elif action == "-D":
            ok = rest in self.chains.get(key, [])
            if ok:
                self.chains[key].remove(rest)
        if not ok and check:
            raise subprocess.CalledProcessError(1, cmd, stderr="simulated failure")
        return subprocess.CompletedProcess(cmd, 0 if ok else 1, stdout="", stderr="")


class ClientIsolationTests(unittest.TestCase):
    def setUp(self):
        self.iptables = _load("app.iptables")
        self.fake = FakeIptables()
        patcher = patch.object(self.iptables.subprocess, "run", self.fake.run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def chain(self, name, binary="iptables"):
        return self.fake.chains.get((binary, "filter", name))

    def test_wifi_clients_reach_only_dhcp_dns_and_ping_on_the_gateway(self):
        self.iptables.ensure_client_isolation("wlan0", "192.168.50.0/24", True, [])
        rules = self.chain("IOT_GATEWAY_IN")
        self.assertTrue(all(r[:2] == ["-i", "wlan0"] for r in rules))
        allowed = [r for r in rules if r[-1] == "RETURN"]
        self.assertEqual(
            [r[2:-2] for r in allowed],
            [
                ["-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED"],
                ["-p", "udp", "--dport", "67"],
                ["-p", "udp", "--dport", "53"],
                ["-p", "tcp", "--dport", "53"],
                ["-p", "icmp"],
            ],
        )
        # The agent's own port is not on the allow list; the chain ends by refusing the rest.
        self.assertNotIn("7000", [arg for r in rules for arg in r])
        self.assertEqual(rules[-1], ["-i", "wlan0", "-j", "REJECT"])
        self.assertEqual(self.chain("INPUT")[0], ["-j", "IOT_GATEWAY_IN"])

    def test_private_networks_are_refused_ahead_of_other_forward_rules(self):
        self.fake.chains[("iptables", "filter", "FORWARD")] = [["-j", "KUBE-FORWARD"]]
        self.iptables.ensure_client_isolation("wlan0", "192.168.50.0/24", True, ["10.20.20.50/32"])
        rules = self.chain("IOT_GATEWAY_ISOLATE")
        self.assertEqual(rules[0], ["-i", "wlan0", "-d", "192.168.50.0/24", "-j", "RETURN"])
        self.assertEqual(rules[1], ["-i", "wlan0", "-d", "10.20.20.50/32", "-j", "RETURN"])
        refused = [r[3] for r in rules if r[-1] == "REJECT"]
        self.assertEqual(refused, list(self.iptables.PRIVATE_CIDRS))
        self.assertIn("10.0.0.0/8", refused)  # LAN, pods and services
        self.assertEqual(self.chain("FORWARD"), [["-j", "IOT_GATEWAY_ISOLATE"], ["-j", "KUBE-FORWARD"]])

    def test_applying_twice_does_not_duplicate_rules(self):
        for _ in range(2):
            self.iptables.ensure_client_isolation("wlan0", "192.168.50.0/24", True, [])
        self.assertEqual(len(self.chain("IOT_GATEWAY_IN")), 7)
        self.assertEqual(self.chain("INPUT").count(["-j", "IOT_GATEWAY_IN"]), 1)
        self.assertEqual(self.chain("FORWARD").count(["-j", "IOT_GATEWAY_ISOLATE"]), 1)

    def test_lan_isolation_can_be_switched_off_but_the_gateway_stays_closed(self):
        self.iptables.ensure_client_isolation("wlan0", "192.168.50.0/24", True, [])
        self.iptables.ensure_client_isolation("wlan0", "192.168.50.0/24", False, [])
        self.assertIsNone(self.chain("IOT_GATEWAY_ISOLATE"))
        self.assertNotIn(["-j", "IOT_GATEWAY_ISOLATE"], self.chain("FORWARD") or [])
        self.assertEqual(len(self.chain("IOT_GATEWAY_IN")), 7)

    def test_ipv6_from_wifi_clients_is_dropped(self):
        warning = self.iptables.ensure_client_isolation("wlan0", "192.168.50.0/24", True, [])
        self.assertIsNone(warning)
        self.assertEqual(self.chain("IOT_GATEWAY_IN6", "ip6tables")[-1], ["-i", "wlan0", "-j", "DROP"])
        self.assertEqual(self.chain("IOT_GATEWAY_FWD6", "ip6tables"), [["-i", "wlan0", "-j", "DROP"]])

    def test_missing_ip6tables_is_a_warning_not_a_failed_apply(self):
        self.fake.fail_binary = "ip6tables"
        warning = self.iptables.ensure_client_isolation("wlan0", "192.168.50.0/24", True, [])
        self.assertIn("IPv6 client isolation not installed", warning)
        self.assertEqual(len(self.chain("IOT_GATEWAY_IN")), 7)

    def test_teardown_removes_every_chain(self):
        self.iptables.ensure_chains()
        self.iptables.ensure_jump_rules()
        self.iptables.ensure_client_isolation("wlan0", "192.168.50.0/24", True, [])
        self.iptables.teardown()
        left = {chain for (_b, _t, chain), rules in self.fake.chains.items() if chain.startswith("IOT_")}
        self.assertEqual(left, set())
        for parent in ("INPUT", "FORWARD"):
            self.assertEqual(self.chain(parent), [])


class SavedBlocksTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        env = patch.dict(os.environ, {"STATE_DIR": self.dir.name})
        env.start()
        self.addCleanup(env.stop)
        self.state = _load("app.state")
        self.runtime = self.state.GatewayRuntime()

    def test_blocks_are_saved_and_put_back_after_a_firewall_reset(self):
        self.runtime.save_blocked("AA:BB:CC:DD:EE:01", True)
        self.runtime.save_blocked("aa:bb:cc:dd:ee:02", True)
        self.runtime.save_blocked("aa:bb:cc:dd:ee:02", False)
        self.assertEqual(json.loads((Path(self.dir.name) / "blocked_devices.json").read_text()), ["aa:bb:cc:dd:ee:01"])

        fake = FakeIptables()
        with patch.object(self.state.iptables.subprocess, "run", fake.run):
            self.state.iptables.ensure_chains()  # an empty block chain, as after a reboot
            self.runtime.restore_blocked()
            self.assertEqual(
                fake.chains[("iptables", "filter", "IOT_DEVICE_BLOCK")],
                [["-m", "mac", "--mac-source", "aa:bb:cc:dd:ee:01", "-j", "DROP"]],
            )

    def test_an_unreadable_block_file_blocks_nothing(self):
        (Path(self.dir.name) / "blocked_devices.json").write_text("{not json")
        self.assertEqual(self.runtime.read_blocked(), [])


class ConfigValidationTests(unittest.TestCase):
    def test_isolation_is_on_by_default_and_exceptions_must_be_ipv4_networks(self):
        models = _load("app.models")
        validate = importlib.import_module("app.validate")
        base = {"ssid": "IoT", "psk": "supersecretpassword"}
        self.assertTrue(models.WifiConfig(**base).isolate_clients_from_lan)
        # A config saved before the field existed restores with isolation on.
        self.assertTrue(models.WifiConfig.model_validate_json(json.dumps(base)).isolate_clients_from_lan)
        with (
            patch.object(validate, "_iface_exists", return_value=True),
            patch.object(validate, "_bin_exists", return_value=True),
        ):
            ok = validate.validate_config(models.WifiConfig(**base, lan_allowed_cidrs=["10.20.20.50/32"]))
            bad = validate.validate_config(models.WifiConfig(**base, lan_allowed_cidrs=["10.20.20.50/24", "fd00::/8"]))
        self.assertTrue(ok.ok, ok.issues)
        self.assertEqual(len(bad.issues), 2, bad.issues)


try:
    from fastapi.testclient import TestClient
except ImportError:  # CI installs the gateway-agent requirements
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi not installed")
class ApiTokenTests(unittest.TestCase):
    def client(self, token: str | None):
        env = {"GATEWAY_AGENT_TOKEN": token} if token else {}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        if not token:
            os.environ.pop("GATEWAY_AGENT_TOKEN", None)
        main = _load("app.main")
        blocked = patch.object(main, "list_blocked", return_value=["aa:bb:cc:dd:ee:01"])
        blocked.start()
        self.addCleanup(blocked.stop)
        return TestClient(main.app)

    def test_every_endpoint_but_health_needs_the_token(self):
        client = self.client("s3cret")
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/blocked").status_code, 401)
        self.assertEqual(client.get("/status").status_code, 401)
        self.assertEqual(client.post("/block", json={"mac": "aa:bb:cc:dd:ee:01"}).status_code, 401)
        self.assertEqual(client.delete("/block/aa:bb:cc:dd:ee:01").status_code, 401)
        self.assertEqual(client.post("/rollback").status_code, 401)
        self.assertEqual(client.post("/apply", json={"ssid": "x", "psk": "12345678"}).status_code, 401)
        self.assertEqual(client.get("/blocked", headers={"Authorization": "Bearer wrong"}).status_code, 401)
        ok = client.get("/blocked", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json(), {"blocked": ["aa:bb:cc:dd:ee:01"]})

    def test_without_a_token_the_api_behaves_as_before(self):
        self.assertEqual(self.client(None).get("/blocked").status_code, 200)


if __name__ == "__main__":
    unittest.main()
