#!/usr/bin/env python3
"""Generate controlled TCP port-sweep traffic for IoT anomaly research.

Run from a device connected to the IoT Wi-Fi so packets pass through the
gateway collector. The script intentionally does not read the API/dashboard;
it only generates traffic and writes local experiment metadata.
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


PROFILES: dict[str, dict[str, object]] = {
    "negative": {
        "ports": [80, 443],
        "rate": 0.5,
        "label": "benign_low_port_activity",
        "description": "Low-volume normal web-like traffic for false-positive checks.",
    },
    "borderline": {
        "ports": [22, 80, 443, 8080, 8443],
        "rate": 1.0,
        "label": "borderline_port_sweep",
        "description": "Near the port_churn threshold; useful for boundary checks.",
    },
    "positive": {
        "ports": [22, 23, 25, 80, 443, 8080, 8443, 3389, 5900, 6379, 27017, 5432, 3306, 1433, 9200, 11211],
        "rate": 4.0,
        "label": "attack_port_sweep",
        "description": "Expected to trigger port_churn: >=6 ports and >=5 new ports.",
    },
    "slow": {
        "ports": [22, 23, 25, 80, 443, 8080, 8443, 3389, 5900, 6379, 27017, 5432, 3306, 1433, 9200, 11211],
        "rate": 0.2,
        "label": "slow_port_sweep",
        "description": "Same signal as positive, spread over time to test bucket sensitivity.",
    },
    "aggressive": {
        "ports": [20, 21, 22, 23, 25, 53, 80, 110, 135, 139, 143, 389, 443, 445, 465, 587, 993, 995, 1433, 1883, 2323, 3306, 3389, 5432, 5683, 5900, 5985, 5986, 6379, 8080, 8443, 9200, 11211, 27017],
        "rate": 10.0,
        "label": "aggressive_port_sweep",
        "description": "High-diversity, high-rate sweep for strong model/heuristic response.",
    },
}


PORT_GROUPS: dict[str, list[int]] = {
    "web": [80, 443, 8080, 8443],
    "iot": [23, 2323, 1883, 5683, 554, 1900, 5353],
    "admin": [22, 23, 3389, 5900, 5985, 5986],
    "db": [1433, 3306, 5432, 6379, 9200, 11211, 27017],
    "common": [20, 21, 22, 23, 25, 53, 80, 110, 135, 139, 143, 389, 443, 445, 993, 995],
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_ports(value: str | None, profile: str) -> list[int]:
    if not value:
        return list(PROFILES[profile]["ports"])  # type: ignore[arg-type]

    ports: list[int] = []
    for token in value.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token in PORT_GROUPS:
            ports.extend(PORT_GROUPS[token])
            continue
        try:
            port = int(token)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"unknown port/group: {token}") from exc
        if not 1 <= port <= 65535:
            raise argparse.ArgumentTypeError(f"port out of range: {port}")
        ports.append(port)

    unique_ports = sorted(set(ports))
    if not unique_ports:
        raise argparse.ArgumentTypeError("at least one port is required")
    return unique_ports


def load_targets(args: argparse.Namespace) -> list[str]:
    targets = [args.target]
    if args.targets_file:
        path = Path(args.targets_file)
        targets = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    if not targets:
        raise SystemExit("no targets provided")
    return targets


def connect_probe(target: str, port: int, timeout: float) -> dict[str, object]:
    started = time.perf_counter()
    outcome = "unknown"
    error = None
    try:
        with socket.create_connection((target, port), timeout=timeout):
            outcome = "open"
    except ConnectionRefusedError as exc:
        outcome = "refused"
        error = str(exc)
    except TimeoutError as exc:
        outcome = "timeout"
        error = str(exc)
    except OSError as exc:
        outcome = "error"
        error = str(exc)
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    record: dict[str, object] = {
        "ts": utc_now(),
        "target": target,
        "port": port,
        "protocol": "tcp",
        "outcome": outcome,
        "duration_ms": duration_ms,
    }
    if error:
        record["error"] = error
    return record


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate controlled TCP port-sweep traffic for IoT anomaly research.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--target", default="192.168.100.1", help="target host/IP to probe")
    parser.add_argument("--targets-file", help="optional file with one target host/IP per line")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="positive", help="traffic profile")
    parser.add_argument("--ports", help="comma-separated ports and/or groups: web,iot,admin,db,common")
    parser.add_argument("--rate", type=float, help="probes per second; defaults to profile rate")
    parser.add_argument("--timeout", type=float, default=0.75, help="TCP connect timeout in seconds")
    parser.add_argument("--repeat", type=int, default=1, help="repeat full target/port sequence")
    parser.add_argument("--jitter", type=float, default=0.1, help="random delay jitter as fraction of base delay")
    parser.add_argument("--randomize", action="store_true", help="shuffle target/port probe order")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without generating traffic")
    parser.add_argument("--label", help="ground-truth label stored in run.json")
    parser.add_argument("--run-id", help="stable run id for output paths")
    parser.add_argument("--out-dir", default="artifacts/port-sweep", help="output directory for experiment logs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rate is not None and args.rate <= 0:
        raise SystemExit("--rate must be positive")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if args.repeat <= 0:
        raise SystemExit("--repeat must be positive")
    if args.jitter < 0:
        raise SystemExit("--jitter cannot be negative")

    targets = load_targets(args)
    ports = parse_ports(args.ports, args.profile)
    rate = float(args.rate or PROFILES[args.profile]["rate"])
    delay = 1.0 / rate
    run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    label = args.label or str(PROFILES[args.profile]["label"])
    run_dir = Path(args.out_dir) / run_id
    probes_path = run_dir / "probes.jsonl"

    planned = [(target, port) for _ in range(args.repeat) for target in targets for port in ports]
    if args.randomize:
        random.shuffle(planned)

    run_payload: dict[str, object] = {
        "run_id": run_id,
        "label": label,
        "profile": args.profile,
        "profile_description": PROFILES[args.profile]["description"],
        "started_at": utc_now(),
        "targets": targets,
        "ports": ports,
        "target_count": len(targets),
        "port_count": len(ports),
        "probe_count": len(planned),
        "rate_per_second": rate,
        "timeout_seconds": args.timeout,
        "repeat": args.repeat,
        "jitter": args.jitter,
        "randomize": args.randomize,
        "dry_run": args.dry_run,
        "expected_signal": "port_churn" if args.profile in {"positive", "slow", "aggressive"} else "none_or_boundary",
    }

    if args.dry_run:
        print(json.dumps(run_payload, indent=2, sort_keys=True))
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "run.json", run_payload)

    counts: dict[str, int] = {"open": 0, "refused": 0, "timeout": 0, "error": 0, "unknown": 0}
    print(f"[port-sweep] run_id={run_id} profile={args.profile} targets={len(targets)} ports={len(ports)} probes={len(planned)}")
    print(f"[port-sweep] output={run_dir}")

    first_probe_at = utc_now()
    for idx, (target, port) in enumerate(planned, start=1):
        record = connect_probe(target, port, args.timeout)
        counts[str(record["outcome"])] = counts.get(str(record["outcome"]), 0) + 1
        append_jsonl(probes_path, record)
        print(f"[{idx:04d}/{len(planned):04d}] {target}:{port} {record['outcome']} {record['duration_ms']}ms")

        if idx < len(planned):
            spread = delay * args.jitter
            sleep_for = max(0.0, delay + random.uniform(-spread, spread))
            time.sleep(sleep_for)

    ended_at = utc_now()
    summary: dict[str, object] = {
        "run_id": run_id,
        "label": label,
        "profile": args.profile,
        "first_probe_at": first_probe_at,
        "ended_at": ended_at,
        "targets": targets,
        "ports": ports,
        "probe_count": len(planned),
        "outcomes": counts,
        "expected_signal": run_payload["expected_signal"],
        "dashboard_note": "Use this run_id and timestamps when reading FP/FN and reaction time from the dashboard.",
    }
    write_json(run_dir / "summary.json", summary)
    print(f"[port-sweep] complete summary={run_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
