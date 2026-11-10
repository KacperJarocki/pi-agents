#!/usr/bin/env python3
"""Benchmark the capture/parser path on a gateway node.

The benchmark intentionally uses the tools already present in the collector
image. It provides a repeatable baseline before replacing the path with a Rust
sensor. Use --dry-run in CI or on a machine without capture privileges.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def build_tcpdump_command(interface: str, output: Path, packet_count: int, snaplen: int) -> list[str]:
    return [
        "tcpdump",
        "-i",
        interface,
        "-n",
        "-p",
        "-s",
        str(snaplen),
        "-w",
        str(output),
        "-c",
        str(packet_count),
    ]


def build_tshark_command(pcap: Path) -> list[str]:
    return ["tshark", "-r", str(pcap), "-T", "fields", "-e", "frame.number"]


def run_benchmark(
    interface: str,
    output: Path,
    packet_count: int,
    snaplen: int,
    duration_seconds: float,
    dry_run: bool = False,
) -> dict:
    tcpdump = build_tcpdump_command(interface, output, packet_count, snaplen)
    tshark = build_tshark_command(output)
    result = {
        "interface": interface,
        "packet_target": packet_count,
        "snaplen": snaplen,
        "duration_seconds": duration_seconds,
        "pcap_path": str(output),
        "tcpdump_command": tcpdump,
        "tshark_command": tshark,
        "dry_run": dry_run,
    }
    if dry_run:
        result.update({"capture_returncode": None, "parsed_packets": None, "pcap_bytes": None})
        return result

    output.parent.mkdir(parents=True, exist_ok=True)
    capture_started = time.perf_counter()
    capture_error = None
    try:
        completed = subprocess.run(
            tcpdump,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=duration_seconds,
            text=True,
        )
        capture_returncode = completed.returncode
        capture_error = completed.stderr.strip() or None
    except subprocess.TimeoutExpired:
        capture_returncode = 124
        capture_error = "capture timed out"
    capture_seconds = time.perf_counter() - capture_started

    parse_started = time.perf_counter()
    parsed_packets = 0
    parse_error = None
    if output.exists() and output.stat().st_size > 0:
        try:
            parsed = subprocess.run(
                tshark,
                check=False,
                capture_output=True,
                text=True,
            )
            parsed_packets = sum(1 for line in parsed.stdout.splitlines() if line.strip())
            parse_error = parsed.stderr.strip() or None
        except OSError as error:
            parse_error = str(error)
    parse_seconds = time.perf_counter() - parse_started

    result.update(
        {
            "capture_returncode": capture_returncode,
            "capture_error": capture_error,
            "capture_seconds": round(capture_seconds, 6),
            "pcap_bytes": output.stat().st_size if output.exists() else 0,
            "parsed_packets": parsed_packets,
            "parse_error": parse_error,
            "parse_seconds": round(parse_seconds, 6),
            "packets_per_second": round(parsed_packets / max(capture_seconds, 1e-9), 2),
        }
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interface", default="wlan0")
    parser.add_argument("--output", type=Path, default=Path("/tmp/pi-agents-benchmark.pcap"))
    parser.add_argument("--packet-count", type=int, default=10000)
    parser.add_argument("--snaplen", type=int, default=128)
    parser.add_argument("--duration-seconds", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", dest="json_path", type=Path)
    args = parser.parse_args()
    result = run_benchmark(
        interface=args.interface,
        output=args.output,
        packet_count=args.packet_count,
        snaplen=args.snaplen,
        duration_seconds=args.duration_seconds,
        dry_run=args.dry_run,
    )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.json_path:
        args.json_path.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
