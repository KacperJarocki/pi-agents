#!/usr/bin/env python3
"""Generate controlled TCP port-sweep traffic for IoT anomaly research.

Run from a device connected to the IoT Wi-Fi so packets pass through the
gateway collector. The script intentionally does not read the API/dashboard;
it only generates traffic and writes local experiment metadata.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import random
import signal
import socket
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from urllib.request import Request, urlopen


PROFILES: dict[str, dict[str, object]] = {
    "negative": {
        "ports": [80, 443],
        "rate": 0.5,
        "duration_seconds": 300.0,
        "label": "benign_low_port_activity",
        "description": "Low-volume normal web-like traffic for false-positive checks.",
    },
    "borderline": {
        "ports": [22, 80, 443, 8080, 8443],
        "rate": 1.0,
        "duration_seconds": 300.0,
        "label": "borderline_port_sweep",
        "description": "Near the port_churn threshold; useful for boundary checks.",
    },
    "positive": {
        "ports": [20, 21, 22, 23, 25, 53, 80, 110, 135, 139, 143, 389, 443, 445, 465, 587, 993, 995, 1433, 1883, 2323, 3306, 3389, 5432, 5683, 5900, 5985, 5986, 6379, 8080, 8443, 9200, 11211, 27017],
        "rate": 6.0,
        "duration_seconds": 300.0,
        "label": "attack_port_sweep",
        "description": "Sustained diverse port sweep intended to trigger port_churn and ML port-diversity features.",
    },
    "slow": {
        "ports": [22, 23, 25, 80, 443, 8080, 8443, 3389, 5900, 6379, 27017, 5432, 3306, 1433, 9200, 11211],
        "rate": 0.2,
        "duration_seconds": 900.0,
        "label": "slow_port_sweep",
        "description": "Same signal as positive, spread over time to test bucket sensitivity.",
    },
    "aggressive": {
        "ports": [20, 21, 22, 23, 25, 53, 80, 110, 135, 139, 143, 389, 443, 445, 465, 587, 993, 995, 1433, 1883, 2323, 3306, 3389, 5432, 5683, 5900, 5985, 5986, 6379, 8080, 8443, 9200, 11211, 27017],
        "rate": 10.0,
        "duration_seconds": 300.0,
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

DISCOVERY_PORTS = [22, 23, 53, 80, 443, 554, 1883, 2323, 8080, 8443]


STOP_REQUESTED = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def request_stop(_signum: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def parse_duration(value: str) -> float:
    raw = value.strip().lower()
    if not raw:
        raise argparse.ArgumentTypeError("duration cannot be empty")
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


def _clean_targets(targets: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for target in targets:
        value = target.strip()
        if not value or value in {"0.0.0.0", "::"} or value in seen:
            continue
        seen.add(value)
        cleaned.append(value)
    return cleaned


def parse_discovery_ports(value: str | None) -> list[int]:
    if not value:
        return list(DISCOVERY_PORTS)
    return parse_ports(value, "aggressive")


def local_ipv4() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]


def discovery_network(value: str) -> tuple[ipaddress.IPv4Network, str | None]:
    if value.strip().lower() == "auto":
        ip_address = local_ipv4()
        octets = ip_address.split(".")
        return ipaddress.ip_network(f"{octets[0]}.{octets[1]}.{octets[2]}.0/24", strict=False), ip_address
    return ipaddress.ip_network(value, strict=False), None


def host_responds(host: str, ports: list[int], timeout: float) -> bool:
    for port in ports:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except ConnectionRefusedError:
            return True
        except TimeoutError:
            continue
        except OSError:
            continue
    return False


def discover_targets(subnet: str, ports: list[int], timeout: float, workers: int, exclude_self: bool) -> list[str]:
    network, detected_self = discovery_network(subnet)
    self_ip = detected_self if exclude_self else None
    hosts = [str(host) for host in network.hosts() if str(host) != self_ip]
    discovered: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(host_responds, host, ports, timeout): host for host in hosts}
        for future in as_completed(futures):
            if future.result():
                discovered.append(futures[future])
    return sorted(discovered, key=lambda ip: tuple(int(part) for part in ip.split(".")))


def _api_url_with_active_filter(url: str, active_only: bool) -> str:
    if not active_only:
        return url
    parsed = urlparse(url)
    query = urlencode({**dict(parse_qsl(parsed.query, keep_blank_values=True)), "active_only": "true"})
    return urlunparse(parsed._replace(query=query))


def load_targets_from_api(url: str, active_only: bool, timeout: float) -> list[str]:
    request_url = _api_url_with_active_filter(url, active_only)
    request = Request(request_url, headers={"Accept": "application/json", "User-Agent": "pi-agents-port-sweep/1.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    devices = payload.get("devices", payload) if isinstance(payload, dict) else payload
    if not isinstance(devices, list):
        raise SystemExit("targets API response must be a list or an object with a devices list")

    targets: list[str] = []
    for device in devices:
        if not isinstance(device, dict):
            continue
        ip_address = str(device.get("ip_address") or "").strip()
        if ip_address:
            targets.append(ip_address)
    return _clean_targets(targets)


def load_targets(args: argparse.Namespace) -> list[str]:
    targets = [args.target]
    if args.targets_file:
        path = Path(args.targets_file)
        targets = [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]
    elif args.targets_api:
        targets = load_targets_from_api(args.targets_api, args.api_active_only, args.api_timeout)
    elif args.discover_subnet:
        targets = discover_targets(
            args.discover_subnet,
            parse_discovery_ports(args.discovery_ports),
            args.discovery_timeout,
            args.discovery_workers,
            not args.discovery_include_self,
        )
    targets = _clean_targets(targets)
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


def format_eta(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{sec:02d}s"
    return f"{minutes}m{sec:02d}s"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate controlled TCP port-sweep traffic for IoT anomaly research.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--target", default="192.168.50.1", help="target host/IP to probe; defaults to the gateway AP IP")
    parser.add_argument("--targets-file", help="optional file with one target host/IP per line")
    parser.add_argument("--targets-api", help="optional gateway API devices URL, e.g. http://localhost:8080/api/v1/devices")
    parser.add_argument("--api-active-only", action="store_true", help="append active_only=true when using --targets-api")
    parser.add_argument("--api-timeout", type=float, default=5.0, help="timeout for loading targets from --targets-api")
    parser.add_argument("--discover-subnet", help="discover targets from a CIDR subnet, or 'auto' for local /24")
    parser.add_argument("--discovery-ports", help="comma-separated ports/groups used only to discover reachable hosts")
    parser.add_argument("--discovery-timeout", type=float, default=0.2, help="TCP timeout per host/port during discovery")
    parser.add_argument("--discovery-workers", type=int, default=64, help="parallel workers for subnet discovery")
    parser.add_argument("--discovery-include-self", action="store_true", help="include this host when --discover-subnet=auto")
    parser.add_argument("--profile", choices=sorted(PROFILES), default="positive", help="traffic profile")
    parser.add_argument("--ports", help="comma-separated ports and/or groups: web,iot,admin,db,common")
    parser.add_argument("--rate", type=float, help="probes per second; defaults to profile rate")
    parser.add_argument("--timeout", type=float, default=0.75, help="TCP connect timeout in seconds")
    parser.add_argument("--duration", type=parse_duration, help="run duration; accepts seconds or suffixes like 10m/1h, use 0 for repeat-only mode")
    parser.add_argument("--repeat", type=int, default=1, help="minimum number of full target/port cycles")
    parser.add_argument("--jitter", type=float, default=0.1, help="random delay jitter as fraction of base delay")
    parser.add_argument("--randomize", action="store_true", help="shuffle target/port probe order")
    parser.add_argument("--seed", type=int, help="random seed for reproducible jitter and shuffled order")
    parser.add_argument("--progress-every", type=int, default=25, help="print progress every N probes; use 0 to disable")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without generating traffic")
    parser.add_argument("--label", help="ground-truth label stored in run.json")
    parser.add_argument("--run-id", help="stable run id for output paths")
    parser.add_argument("--out-dir", default="artifacts/port-sweep", help="output directory for experiment logs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if args.rate is not None and args.rate <= 0:
        raise SystemExit("--rate must be positive")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be positive")
    if args.api_timeout <= 0:
        raise SystemExit("--api-timeout must be positive")
    if args.discovery_timeout <= 0:
        raise SystemExit("--discovery-timeout must be positive")
    if args.discovery_workers <= 0:
        raise SystemExit("--discovery-workers must be positive")
    if args.repeat <= 0:
        raise SystemExit("--repeat must be positive")
    target_source_count = sum(bool(value) for value in (args.targets_file, args.targets_api, args.discover_subnet))
    if target_source_count > 1:
        raise SystemExit("use only one of --targets-file, --targets-api, or --discover-subnet")
    if args.duration is not None and args.duration < 0:
        raise SystemExit("--duration cannot be negative")
    if args.jitter < 0:
        raise SystemExit("--jitter cannot be negative")
    if args.progress_every < 0:
        raise SystemExit("--progress-every cannot be negative")

    if args.seed is not None:
        random.seed(args.seed)

    targets = load_targets(args)
    ports = parse_ports(args.ports, args.profile)
    rate = float(args.rate or PROFILES[args.profile]["rate"])
    duration_seconds = float(args.duration if args.duration is not None else PROFILES[args.profile]["duration_seconds"])
    delay = 1.0 / rate
    run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    label = args.label or str(PROFILES[args.profile]["label"])
    run_dir = Path(args.out_dir) / run_id
    probes_path = run_dir / "probes.jsonl"
    markers_path = run_dir / "markers.jsonl"

    base_sequence = [(target, port) for target in targets for port in ports]
    estimated_probe_count = len(base_sequence) * args.repeat
    if duration_seconds > 0:
        estimated_probe_count = max(estimated_probe_count, int(duration_seconds * rate))
    estimated_end_at = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds) if duration_seconds > 0 else None

    run_payload: dict[str, object] = {
        "run_id": run_id,
        "label": label,
        "profile": args.profile,
        "profile_description": PROFILES[args.profile]["description"],
        "started_at": utc_now(),
        "targets": targets,
        "target_source": "discovery" if args.discover_subnet else "api" if args.targets_api else "file" if args.targets_file else "single",
        "discover_subnet": args.discover_subnet,
        "discovery_ports": parse_discovery_ports(args.discovery_ports) if args.discover_subnet else None,
        "ports": ports,
        "target_count": len(targets),
        "port_count": len(ports),
        "estimated_probe_count": estimated_probe_count,
        "rate_per_second": rate,
        "duration_seconds": duration_seconds,
        "timeout_seconds": args.timeout,
        "repeat": args.repeat,
        "jitter": args.jitter,
        "randomize": args.randomize,
        "seed": args.seed,
        "progress_every": args.progress_every,
        "estimated_end_at": estimated_end_at.isoformat(timespec="milliseconds") if estimated_end_at else None,
        "dry_run": args.dry_run,
        "expected_signal": "port_churn" if args.profile in {"positive", "slow", "aggressive"} else "none_or_boundary",
    }

    if args.dry_run:
        print(json.dumps(run_payload, indent=2, sort_keys=True))
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "run.json", run_payload)
    append_jsonl(markers_path, {"ts": utc_now(), "event": "run_start", "run_id": run_id, "profile": args.profile})

    counts: dict[str, int] = {"open": 0, "refused": 0, "timeout": 0, "error": 0, "unknown": 0}
    print(
        f"[port-sweep] run_id={run_id} profile={args.profile} targets={len(targets)} "
        f"ports={len(ports)} duration={duration_seconds}s rate={rate}/s"
    )
    print(f"[port-sweep] output={run_dir}")

    first_probe_at = utc_now()
    end_at = time.monotonic() + duration_seconds if duration_seconds > 0 else None
    started_monotonic = time.monotonic()
    probe_count = 0
    cycle_count = 0
    interrupted = False
    while not STOP_REQUESTED:
        now_monotonic = time.monotonic()
        if cycle_count >= args.repeat and (end_at is None or now_monotonic >= end_at):
            break

        cycle_count += 1
        sequence = list(base_sequence)
        if args.randomize:
            random.shuffle(sequence)
        append_jsonl(markers_path, {"ts": utc_now(), "event": "cycle_start", "cycle": cycle_count})

        for target, port in sequence:
            if STOP_REQUESTED:
                interrupted = True
                break
            if cycle_count > args.repeat and end_at is not None and time.monotonic() >= end_at:
                break

            probe_count += 1
            record = connect_probe(target, port, args.timeout)
            counts[str(record["outcome"])] = counts.get(str(record["outcome"]), 0) + 1
            append_jsonl(probes_path, record)
            should_print = args.progress_every == 0 or probe_count == 1 or probe_count % args.progress_every == 0
            if should_print:
                if end_at is not None:
                    remaining = max(0.0, end_at - time.monotonic())
                    eta = format_eta(remaining)
                    progress = min(100.0, (time.monotonic() - started_monotonic) / max(duration_seconds, 1e-9) * 100.0)
                    suffix = f" progress={progress:.1f}% eta={eta}"
                else:
                    suffix = ""
                print(f"[{probe_count:05d}] cycle={cycle_count} {target}:{port} {record['outcome']} {record['duration_ms']}ms{suffix}")

            spread = delay * args.jitter
            sleep_for = max(0.0, delay + random.uniform(-spread, spread))
            if end_at is not None:
                sleep_for = min(sleep_for, max(0.0, end_at - time.monotonic()))
            time.sleep(sleep_for)

        append_jsonl(markers_path, {"ts": utc_now(), "event": "cycle_end", "cycle": cycle_count, "probe_count": probe_count})

    if STOP_REQUESTED:
        interrupted = True

    ended_at = utc_now()
    summary: dict[str, object] = {
        "run_id": run_id,
        "label": label,
        "profile": args.profile,
        "first_probe_at": first_probe_at,
        "ended_at": ended_at,
        "targets": targets,
        "ports": ports,
        "probe_count": probe_count,
        "cycle_count": cycle_count,
        "duration_seconds": duration_seconds,
        "interrupted": interrupted,
        "outcomes": counts,
        "expected_signal": run_payload["expected_signal"],
        "dashboard_note": "Use this run_id and timestamps when reading FP/FN and reaction time from the dashboard.",
    }
    append_jsonl(markers_path, {"ts": ended_at, "event": "run_end", "run_id": run_id, "interrupted": interrupted, "probe_count": probe_count})
    write_json(run_dir / "summary.json", summary)
    status = "interrupted" if interrupted else "complete"
    print(f"[port-sweep] {status} summary={run_dir / 'summary.json'}")
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
