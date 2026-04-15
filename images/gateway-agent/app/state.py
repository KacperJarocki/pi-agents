from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from dataclasses import dataclass
import ipaddress
from pathlib import Path

import structlog

from .models import WifiConfig
from .render import render_dnsmasq, render_hostapd
from . import iptables

log = structlog.get_logger()


def _state_dir() -> Path:
    return Path(os.getenv("STATE_DIR", "/data"))


def _paths() -> dict[str, Path]:
    d = _state_dir()
    return {
        "dir": d,
        "lock": d / "lock",
        "config": d / "wifi_config.json",
        "hostapd": d / "hostapd.conf",
        "dnsmasq": d / "dnsmasq.conf",
        "leases": d / "dnsmasq.leases",
        "last_apply": d / "last_apply.json",
        "lkg": d / "last_known_good",
    }


def build_hostapd_command(config_path: Path) -> list[str]:
    return [
        "hostapd",
        str(config_path),
    ]


def build_dnsmasq_command(config_path: Path) -> list[str]:
    return [
        "dnsmasq",
        "--no-daemon",
        f"--conf-file={config_path}",
    ]


@dataclass
class ManagedProcess:
    name: str
    proc: asyncio.subprocess.Process

    @property
    def pid(self) -> int | None:
        return self.proc.pid

    @property
    def running(self) -> bool:
        return self.proc.returncode is None

    @property
    def exit_code(self) -> int | None:
        return self.proc.returncode


class GatewayRuntime:
    def __init__(self):
        self._hostapd: ManagedProcess | None = None
        self._dnsmasq: ManagedProcess | None = None
        self._lock = asyncio.Lock()
        self._hostapd_error: str | None = None
        self._dnsmasq_error: str | None = None

    def read_last_apply(self) -> tuple[bool | None, str | None]:
        p = _paths()["last_apply"]
        try:
            obj = json.loads(p.read_text())
            return obj.get("ok"), obj.get("message")
        except Exception:
            return None, None

    def read_config(self) -> WifiConfig | None:
        p = _paths()["config"]
        try:
            return WifiConfig.model_validate_json(p.read_text())
        except Exception:
            return None

    def read_leases(self) -> list[dict]:
        p = _paths()["leases"]
        if not p.exists():
            return []

        clients: list[dict] = []
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue

            parts = line.split()
            if len(parts) < 5:
                continue

            expiry, mac, ip, hostname, client_id = parts[:5]
            clients.append(
                {
                    "lease_expires_at": expiry,
                    "mac_address": None if mac == "*" else mac,
                    "ip_address": ip,
                    "hostname": None if hostname == "*" else hostname,
                    "client_id": None if client_id == "*" else client_id,
                }
            )

        return clients

    async def apply(self, cfg: WifiConfig) -> tuple[bool, str]:
        async with self._lock:
            return await self._apply_locked(cfg)

    async def rollback(self) -> tuple[bool, str]:
        async with self._lock:
            return await self._rollback_locked()

    async def stop(self) -> None:
        async with self._lock:
            log.info("gateway_runtime_stop_begin")
            await self._stop_processes()
            log.info("gateway_runtime_stop_complete")

    async def restore_from_disk(self) -> tuple[bool, str]:
        async with self._lock:
            paths = _paths()
            lkg = paths["lkg"]
            if lkg.exists() and (lkg / paths["config"].name).exists():
                try:
                    cfg = WifiConfig.model_validate_json((lkg / paths["config"].name).read_text())
                    ok, msg = await self._apply_locked(cfg)
                    return ok, f"restore(lkg): {msg}"
                except Exception as e:
                    return False, f"restore(lkg) failed: {e}"

            cfg = self.read_config()
            if not cfg:
                return False, "no saved config"

            try:
                ok, msg = await self._apply_locked(cfg)
                return ok, f"restore(config): {msg}"
            except Exception as e:
                return False, f"restore(config) failed: {e}"

    def process_status(self) -> dict:
        def _ps(p: ManagedProcess | None):
            if not p:
                return {"running": False, "pid": None, "exit_code": None, "last_error": None}
            err = self._hostapd_error if p.name == "hostapd" else self._dnsmasq_error
            return {"running": p.running, "pid": p.pid, "exit_code": p.exit_code, "last_error": err}

        return {
            "hostapd": _ps(self._hostapd),
            "dnsmasq": _ps(self._dnsmasq),
        }

    async def _apply_locked(self, cfg: WifiConfig) -> tuple[bool, str]:
        paths = _paths()
        paths["dir"].mkdir(parents=True, exist_ok=True)
        self._hostapd_error = None
        self._dnsmasq_error = None

        # Persist intent and rendered configs.
        paths["config"].write_text(cfg.model_dump_json())
        paths["hostapd"].write_text(render_hostapd(cfg))
        paths["dnsmasq"].write_text(render_dnsmasq(cfg))

        # Persist intent and rendered configs even if disabled (for UI).
        if not cfg.enabled:
            await self._stop_processes()
            iptables.teardown()
            # Remove IPv4 address from AP interface, but don't touch eth0.
            await _run(["ip", "-4", "addr", "flush", "dev", cfg.ap_interface])
            msg = "gateway disabled"
            await self._write_last_apply(True, msg)
            return True, msg

        # Configure interface and forwarding.
        net = ipaddress.ip_network(cfg.subnet_cidr, strict=True)
        await _run(["ip", "link", "set", cfg.ap_interface, "up"])
        await _run(
            [
                "ip",
                "addr",
                "replace",
                f"{cfg.gateway_ip}/{net.prefixlen}",
                "dev",
                cfg.ap_interface,
            ]
        )
        await _run(["sysctl", "-w", "net.ipv4.ip_forward=1"])

        # Configure NAT rules in a dedicated chain (safe for kube node).
        iptables.ensure_chains()
        iptables.ensure_jump_rules()
        iptables.ensure_nat_rules(cfg.subnet_cidr, cfg.ap_interface, cfg.upstream_interface)

        # Restart processes.
        await self._stop_processes()
        await self._start_dnsmasq(cfg)
        await self._start_hostapd(cfg)

        # Basic health: ensure both processes are running shortly after start.
        await asyncio.sleep(1.0)
        if not self._dnsmasq or not self._dnsmasq.running:
            msg = f"dnsmasq failed to start: {self._dnsmasq_error or 'unknown error'}"
            await self._write_last_apply(False, msg)
            return False, msg
        if not self._hostapd or not self._hostapd.running:
            msg = f"hostapd failed to start: {self._hostapd_error or 'unknown error'}"
            await self._write_last_apply(False, msg)
            return False, msg

        # Save last-known-good snapshot.
        lkg = paths["lkg"]
        lkg.mkdir(parents=True, exist_ok=True)
        shutil.copy2(paths["config"], lkg / paths["config"].name)
        shutil.copy2(paths["hostapd"], lkg / paths["hostapd"].name)
        shutil.copy2(paths["dnsmasq"], lkg / paths["dnsmasq"].name)

        msg = "ap applied"
        await self._write_last_apply(True, msg)
        return True, msg

    async def _rollback_locked(self) -> tuple[bool, str]:
        paths = _paths()
        lkg = paths["lkg"]
        if not lkg.exists():
            # No known-good config; just stop services and tear down our rules.
            await self._stop_processes()
            iptables.teardown()
            msg = "no last-known-good config; services stopped"
            await self._write_last_apply(False, msg)
            return False, msg

        try:
            cfg = WifiConfig.model_validate_json((lkg / paths["config"].name).read_text())
        except Exception:
            await self._stop_processes()
            iptables.teardown()
            msg = "invalid last-known-good config; services stopped"
            await self._write_last_apply(False, msg)
            return False, msg

        # Re-apply the last-known-good config.
        ok, msg = await self._apply_locked(cfg)
        return ok, f"rollback: {msg}"

    async def _write_last_apply(self, ok: bool, message: str) -> None:
        p = _paths()["last_apply"]
        p.write_text(json.dumps({"ok": ok, "message": message, "ts": time.time()}))

    async def _stop_processes(self) -> None:
        await _terminate(self._hostapd)
        await _terminate(self._dnsmasq)
        self._hostapd = None
        self._dnsmasq = None

    async def _start_hostapd(self, cfg: WifiConfig) -> None:
        paths = _paths()
        proc = await asyncio.create_subprocess_exec(
            *build_hostapd_command(paths["hostapd"]),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._hostapd = ManagedProcess("hostapd", proc)
        asyncio.create_task(self._capture_process_error("hostapd", proc))

    async def _start_dnsmasq(self, cfg: WifiConfig) -> None:
        paths = _paths()
        proc = await asyncio.create_subprocess_exec(
            *build_dnsmasq_command(paths["dnsmasq"]),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        self._dnsmasq = ManagedProcess("dnsmasq", proc)
        asyncio.create_task(self._capture_process_error("dnsmasq", proc))

    async def _capture_process_error(self, name: str, proc: asyncio.subprocess.Process) -> None:
        if not proc.stderr:
            return
        try:
            data = await proc.stderr.read()
        except Exception as e:
            data = str(e).encode()

        msg = data.decode(errors="replace").strip() if data else None
        if name == "hostapd":
            self._hostapd_error = msg
        else:
            self._dnsmasq_error = msg
        if msg:
            log.warning("managed_process_stderr", process=name, error=msg)


async def _run(cmd: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}: {err.decode().strip()}")
    if out:
        log.info("cmd_output", cmd=cmd[0], out=out.decode().strip())


async def _terminate(p: ManagedProcess | None) -> None:
    if not p:
        return
    log.info("managed_process_stop_begin", process=p.name, pid=p.pid)
    if p.proc.returncode is None:
        p.proc.terminate()
        try:
            await asyncio.wait_for(p.proc.wait(), timeout=5)
            log.info("managed_process_stopped", process=p.name, pid=p.pid, exit_code=p.proc.returncode)
        except TimeoutError:
            log.warning("managed_process_stop_timeout", process=p.name, pid=p.pid)
            p.proc.kill()
            await p.proc.wait()
            log.warning("managed_process_killed", process=p.name, pid=p.pid, exit_code=p.proc.returncode)
    else:
        log.info("managed_process_already_stopped", process=p.name, pid=p.pid, exit_code=p.proc.returncode)
