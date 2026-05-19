#!/usr/bin/env python3
"""Generate benign IoT-like traffic for baseline ML experiments.

Run from a device connected to the IoT Wi-Fi. The script does not call the
project API; it only emits predictable network traffic and writes local run
metadata for later correlation with dashboard readings.
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import ssl
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse


PROFILES: dict[str, dict[str, object]] = {
    "sensor": {
        "duration_seconds": 600.0,
        "interval_seconds": 30.0,
        "jitter": 0.2,
        "endpoints": ["https://www.cloudflare.com/cdn-cgi/trace", "https://example.com/"],
        "dns_hosts": ["pool.ntp.org", "example.com", "www.cloudflare.com"],
        "udp_targets": ["1.1.1.1:123"],
        "label": "benign_iot_sensor",
        "description": "Low-volume periodic telemetry baseline.",
    },
    "plug": {
        "duration_seconds": 600.0,
        "interval_seconds": 20.0,
        "jitter": 0.25,
        "endpoints": ["https://example.com/", "https://www.google.com/generate_204"],
        "dns_hosts": ["example.com", "www.google.com"],
        "udp_targets": [],
        "label": "benign_smart_plug",
        "description": "Small cloud check-ins with light DNS.",
    },
    "camera-idle": {
        "duration_seconds": 600.0,
        "interval_seconds": 10.0,
        "jitter": 0.15,
        "endpoints": ["https://example.com/", "https://www.cloudflare.com/cdn-cgi/trace"],
        "dns_hosts": ["example.com", "www.cloudflare.com", "time.cloudflare.com"],
        "udp_targets": ["1.1.1.1:123"],
        "label": "benign_camera_idle",
        "description": "Idle camera-like cloud keepalive traffic, not video streaming.",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_duration(value: str) -> float:
    raw = value.strip().lower()
    multipliers = {"s": 1.0, "m": 60.0, "h": 3600.0}
    suffix = raw[-1]
    if suffix in multipliers:
        raw_number = raw[:-1]
        multiplier = multipliers[suffix]
    else:
        raw_number = raw
        multiplier = 1.0
    try:
        duration = float(raw_number) * multiplier
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid duration: {value}") from exc
    if duration < 0:
        raise argparse.ArgumentTypeError("duration cannot be negative")
    return duration


def split_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def resolve_host(host: str, timeout: float) -> dict[str, object]:
    started = time.perf_counter()
    outcome = "ok"
    addresses: list[str] = []
    error = None
    original_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout)
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        addresses = sorted({info[4][0] for info in infos})[:5]
    except OSError as exc:
        outcome = "error"
        error = str(exc)
    finally:
        socket.setdefaulttimeout(original_timeout)
    record: dict[str, object] = {
        "ts": utc_now(),
        "action": "dns_lookup",
        "host": host,
        "outcome": outcome,
        "addresses": addresses,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if error:
        record["error"] = error
    return record


def http_get(url: str, timeout: float, max_bytes: int) -> dict[str, object]:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    host = parsed.hostname
    if not host:
        return {"ts": utc_now(), "action": "http_get", "url": url, "outcome": "error", "error": "missing host"}

    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    started = time.perf_counter()
    outcome = "ok"
    status = None
    bytes_read = 0
    error = None
    try:
        raw_sock = socket.create_connection((host, port), timeout=timeout)
        with raw_sock:
            if scheme == "https":
                context = ssl.create_default_context()
                sock = context.wrap_socket(raw_sock, server_hostname=host)
            else:
                sock = raw_sock
            with sock:
                request = (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: {host}\r\n"
                    "User-Agent: pi-agents-iot-emulator/1.0\r\n"
                    "Accept: */*\r\n"
                    "Connection: close\r\n\r\n"
                )
                sock.sendall(request.encode("ascii"))
                response = sock.recv(max_bytes)
                bytes_read = len(response)
                first_line = response.split(b"\r\n", 1)[0].decode("ascii", errors="replace") if response else ""
                parts = first_line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    status = int(parts[1])
    except OSError as exc:
        outcome = "error"
        error = str(exc)

    record: dict[str, object] = {
        "ts": utc_now(),
        "action": "http_get",
        "url": url,
        "host": host,
        "port": port,
        "outcome": outcome,
        "status": status,
        "bytes_read": bytes_read,
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if error:
        record["error"] = error
    return record


def udp_heartbeat(target: str, timeout: float, payload: bytes) -> dict[str, object]:
    host, _, port_raw = target.rpartition(":")
    if not host or not port_raw.isdigit():
        return {"ts": utc_now(), "action": "udp_heartbeat", "target": target, "outcome": "error", "error": "expected host:port"}
    port = int(port_raw)
    started = time.perf_counter()
    outcome = "sent"
    error = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(payload, (host, port))
    except OSError as exc:
        outcome = "error"
        error = str(exc)
    record: dict[str, object] = {
        "ts": utc_now(),
        "action": "udp_heartbeat",
        "target": target,
        "host": host,
        "port": port,
        "outcome": outcome,
        "bytes_sent": len(payload),
        "duration_ms": round((time.perf_counter() - started) * 1000, 3),
    }
    if error:
        record["error"] = error
    return record


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate benign IoT-like traffic for baseline experiments.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--profile", choices=sorted(PROFILES), default="sensor", help="IoT traffic profile")
    parser.add_argument("--duration", type=parse_duration, help="run duration; accepts seconds or suffixes like 10m/1h")
    parser.add_argument("--interval", type=float, help="seconds between telemetry cycles")
    parser.add_argument("--jitter", type=float, help="random delay jitter as fraction of interval")
    parser.add_argument("--endpoints", help="comma-separated HTTP/HTTPS telemetry endpoints")
    parser.add_argument("--dns-hosts", help="comma-separated hostnames to resolve each cycle")
    parser.add_argument("--udp-targets", help="comma-separated UDP heartbeats as host:port")
    parser.add_argument("--timeout", type=float, default=3.0, help="network operation timeout in seconds")
    parser.add_argument("--max-bytes", type=int, default=2048, help="maximum HTTP response bytes to read")
    parser.add_argument("--seed", type=int, help="random seed for reproducible endpoint selection")
    parser.add_argument("--dry-run", action="store_true", help="print plan without generating traffic")
    parser.add_argument("--run-id", help="stable run id for output paths")
    parser.add_argument("--out-dir", default="artifacts/iot-emulator", help="output directory for run logs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    profile = PROFILES[args.profile]
    if args.seed is not None:
        random.seed(args.seed)
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if args.max_bytes <= 0:
        raise SystemExit("--max-bytes must be positive")

    duration = float(args.duration if args.duration is not None else profile["duration_seconds"])
    interval = float(args.interval if args.interval is not None else profile["interval_seconds"])
    jitter = float(args.jitter if args.jitter is not None else profile["jitter"])
    if duration < 0 or interval <= 0 or jitter < 0:
        raise SystemExit("duration/jitter must be non-negative and interval must be positive")

    endpoints = split_csv(args.endpoints) or list(profile["endpoints"])  # type: ignore[arg-type]
    dns_hosts = split_csv(args.dns_hosts) or list(profile["dns_hosts"])  # type: ignore[arg-type]
    udp_targets = split_csv(args.udp_targets) or list(profile["udp_targets"])  # type: ignore[arg-type]
    run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = Path(args.out_dir) / run_id
    events_path = run_dir / "events.jsonl"
    estimated_cycles = 0 if duration == 0 else max(1, int(duration / interval))
    estimated_end_at = datetime.now(timezone.utc) + timedelta(seconds=duration) if duration > 0 else None

    run_payload: dict[str, object] = {
        "run_id": run_id,
        "profile": args.profile,
        "label": profile["label"],
        "description": profile["description"],
        "started_at": utc_now(),
        "estimated_end_at": estimated_end_at.isoformat(timespec="milliseconds") if estimated_end_at else None,
        "duration_seconds": duration,
        "interval_seconds": interval,
        "jitter": jitter,
        "endpoints": endpoints,
        "dns_hosts": dns_hosts,
        "udp_targets": udp_targets,
        "estimated_cycles": estimated_cycles,
        "seed": args.seed,
        "dry_run": args.dry_run,
    }

    if args.dry_run:
        print(json.dumps(run_payload, indent=2, sort_keys=True))
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "run.json", run_payload)
    print(f"[iot-emulator] run_id={run_id} profile={args.profile} duration={duration}s interval={interval}s")
    print(f"[iot-emulator] output={run_dir}")

    end_at = time.monotonic() + duration if duration > 0 else time.monotonic()
    cycle = 0
    counts: dict[str, int] = {"dns_lookup": 0, "http_get": 0, "udp_heartbeat": 0, "error": 0}
    while duration == 0 and cycle == 0 or duration > 0 and time.monotonic() < end_at:
        cycle += 1
        cycle_started = utc_now()
        append_jsonl(events_path, {"ts": cycle_started, "event": "cycle_start", "cycle": cycle})

        for host in dns_hosts:
            record = resolve_host(host, args.timeout)
            record["cycle"] = cycle
            append_jsonl(events_path, record)
            counts["dns_lookup"] += 1
            if record["outcome"] == "error":
                counts["error"] += 1

        if endpoints:
            endpoint = random.choice(endpoints)
            record = http_get(endpoint, args.timeout, args.max_bytes)
            record["cycle"] = cycle
            append_jsonl(events_path, record)
            counts["http_get"] += 1
            if record["outcome"] == "error":
                counts["error"] += 1

        for target in udp_targets:
            payload = f"iot-heartbeat run={run_id} cycle={cycle}\n".encode("ascii")
            record = udp_heartbeat(target, args.timeout, payload)
            record["cycle"] = cycle
            append_jsonl(events_path, record)
            counts["udp_heartbeat"] += 1
            if record["outcome"] == "error":
                counts["error"] += 1

        append_jsonl(events_path, {"ts": utc_now(), "event": "cycle_end", "cycle": cycle})
        print(f"[iot-emulator] cycle={cycle} dns={len(dns_hosts)} http={1 if endpoints else 0} udp={len(udp_targets)}")

        if duration == 0:
            break
        spread = interval * jitter
        sleep_for = max(0.0, interval + random.uniform(-spread, spread))
        sleep_for = min(sleep_for, max(0.0, end_at - time.monotonic()))
        time.sleep(sleep_for)

    summary: dict[str, object] = {
        "run_id": run_id,
        "profile": args.profile,
        "ended_at": utc_now(),
        "duration_seconds": duration,
        "cycle_count": cycle,
        "counts": counts,
        "dashboard_note": "Use these timestamps as benign IoT baseline windows when comparing FP/FN and model reaction.",
    }
    write_json(run_dir / "summary.json", summary)
    print(f"[iot-emulator] complete summary={run_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
