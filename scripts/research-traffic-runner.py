#!/usr/bin/env python3
"""Run the port-sweep traffic research protocol with one command.

By default this runs only port-sweep profiles. The optional ``normal`` phase is
available when the benign IoT emulator should be part of the same experiment.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


STOP_REQUESTED = False


DEFAULT_PHASES = ["negative", "borderline", "positive", "slow", "aggressive"]
PORT_SWEEP_PHASES = {"negative", "borderline", "positive", "slow", "aggressive"}


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


def split_csv(value: str) -> list[str]:
    phases = [item.strip() for item in value.split(",") if item.strip()]
    allowed = {"normal", *PORT_SWEEP_PHASES}
    unknown = sorted(set(phases) - allowed)
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown phase(s): {', '.join(unknown)}")
    if not phases:
        raise argparse.ArgumentTypeError("at least one phase is required")
    return phases


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the port-sweep traffic research protocol with one command.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--target", default="192.168.100.1", help="single target for port-sweep phases")
    parser.add_argument("--targets-file", help="optional target file passed to port-sweep phases")
    parser.add_argument("--targets-api", help="optional gateway API devices URL passed to port-sweep phases")
    parser.add_argument("--api-active-only", action="store_true", help="pass active_only=true to --targets-api")
    parser.add_argument("--phases", type=split_csv, default=DEFAULT_PHASES, help="comma-separated phases: negative,borderline,positive,slow,aggressive; add normal if needed")
    parser.add_argument("--normal-profile", choices=["sensor", "plug", "camera-idle"], default="sensor", help="benign emulator profile for the normal phase")
    parser.add_argument("--normal-duration", type=parse_duration, default=parse_duration("10m"), help="duration for the normal baseline phase")
    parser.add_argument("--gap", type=parse_duration, default=parse_duration("60s"), help="quiet gap between phases")
    parser.add_argument("--seed", type=int, help="base random seed; each phase gets seed+phase_index")
    parser.add_argument("--randomize", action="store_true", help="randomize port-sweep probe order")
    parser.add_argument("--dry-run", action="store_true", help="print the full plan without generating traffic")
    parser.add_argument("--run-id", help="stable research run id")
    parser.add_argument("--out-dir", default="artifacts/research-runs", help="output directory for research run metadata")
    parser.add_argument("--child-out-root", default="artifacts", help="root directory for child script artifact folders")
    return parser


def phase_command(args: argparse.Namespace, repo_root: Path, run_dir: Path, phase: str, index: int) -> list[str]:
    phase_run_id = f"{args.run_id}-{index:02d}-{phase}"
    seed_args = ["--seed", str(args.seed + index)] if args.seed is not None else []
    if phase == "normal":
        return [
            sys.executable,
            str(repo_root / "scripts" / "iot-device-emulator.py"),
            "--profile", args.normal_profile,
            "--duration", str(args.normal_duration),
            "--run-id", phase_run_id,
            "--out-dir", str(Path(args.child_out_root) / "iot-emulator"),
            *seed_args,
        ]

    target_args = ["--target", args.target]
    if args.targets_file:
        target_args = ["--targets-file", args.targets_file]
    elif args.targets_api:
        target_args = ["--targets-api", args.targets_api]
        if args.api_active_only:
            target_args.append("--api-active-only")

    command = [
        sys.executable,
        str(repo_root / "scripts" / "port-sweep.py"),
        *target_args,
        "--profile", phase,
        "--run-id", phase_run_id,
        "--out-dir", str(Path(args.child_out_root) / "port-sweep"),
        *seed_args,
    ]
    if args.randomize:
        command.append("--randomize")
    return command


def run_phase(command: list[str], dry_run: bool) -> int:
    if dry_run:
        print(" ".join(command))
        return 0
    return subprocess.run(command).returncode


def sleep_gap(seconds: float, markers_path: Path, after_phase: str, dry_run: bool) -> None:
    if seconds <= 0:
        return
    append_jsonl(markers_path, {"ts": utc_now(), "event": "gap_start", "after_phase": after_phase, "duration_seconds": seconds})
    if dry_run:
        print(f"[research-runner] gap after {after_phase}: {seconds}s")
        return
    end_at = time.monotonic() + seconds
    while not STOP_REQUESTED and time.monotonic() < end_at:
        time.sleep(min(1.0, max(0.0, end_at - time.monotonic())))
    append_jsonl(markers_path, {"ts": utc_now(), "event": "gap_end", "after_phase": after_phase})


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    if args.targets_file and args.targets_api:
        raise SystemExit("use either --targets-file or --targets-api, not both")

    repo_root = Path(__file__).resolve().parents[1]
    args.run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = Path(args.out_dir) / args.run_id
    markers_path = run_dir / "markers.jsonl"
    phase_records: list[dict[str, object]] = []

    commands = [
        {
            "phase": phase,
            "command": phase_command(args, repo_root, run_dir, phase, index),
        }
        for index, phase in enumerate(args.phases, start=1)
    ]
    manifest: dict[str, object] = {
        "run_id": args.run_id,
        "started_at": utc_now(),
        "phases": args.phases,
        "normal_profile": args.normal_profile,
        "normal_duration_seconds": args.normal_duration,
        "gap_seconds": args.gap,
        "target": args.target,
        "targets_file": args.targets_file,
        "targets_api": args.targets_api,
        "api_active_only": args.api_active_only,
        "randomize": args.randomize,
        "seed": args.seed,
        "dry_run": args.dry_run,
        "commands": commands,
        "dashboard_note": "Use markers.jsonl phase_start/phase_end timestamps to collect FP/FN, reaction time, model scores, and final TP/FP/FN/TN from the dashboard. Run benign IoT traffic separately unless the normal phase is explicitly included.",
    }

    if args.dry_run:
        print(json.dumps(manifest, indent=2, sort_keys=True))
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "manifest.json", manifest)
    append_jsonl(markers_path, {"ts": utc_now(), "event": "research_run_start", "run_id": args.run_id})
    print(f"[research-runner] run_id={args.run_id} phases={','.join(args.phases)}")
    print(f"[research-runner] output={run_dir}")

    exit_code = 0
    interrupted = False
    for idx, item in enumerate(commands):
        phase = str(item["phase"])
        command = list(item["command"])
        if STOP_REQUESTED:
            interrupted = True
            break
        started_at = utc_now()
        append_jsonl(markers_path, {"ts": started_at, "event": "phase_start", "phase": phase, "command": command})
        print(f"[research-runner] phase_start {phase}")
        rc = run_phase(command, args.dry_run)
        ended_at = utc_now()
        append_jsonl(markers_path, {"ts": ended_at, "event": "phase_end", "phase": phase, "returncode": rc})
        phase_records.append({"phase": phase, "started_at": started_at, "ended_at": ended_at, "returncode": rc, "command": command})
        print(f"[research-runner] phase_end {phase} rc={rc}")
        if rc != 0:
            exit_code = rc
            break
        if idx < len(commands) - 1:
            sleep_gap(args.gap, markers_path, phase, args.dry_run)

    if STOP_REQUESTED:
        interrupted = True
        exit_code = 130

    summary: dict[str, object] = {
        "run_id": args.run_id,
        "ended_at": utc_now(),
        "interrupted": interrupted,
        "exit_code": exit_code,
        "phase_count": len(phase_records),
        "phases": phase_records,
        "markers": str(markers_path),
        "dashboard_note": manifest["dashboard_note"],
    }
    append_jsonl(markers_path, {"ts": summary["ended_at"], "event": "research_run_end", "run_id": args.run_id, "exit_code": exit_code, "interrupted": interrupted})
    write_json(run_dir / "summary.json", summary)
    print(f"[research-runner] complete summary={run_dir / 'summary.json'}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
