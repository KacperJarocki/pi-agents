#!/usr/bin/env python3
"""Offline model backtesting for FP/FN research windows.

This script loads historical traffic_flows for one device, loads either a
versioned model_registry artifact or an explicit .joblib, and scores the window
without modifying live device risk tables.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "images" / "ml-pipeline"))

from app.ml_core import FeatureExtractor, get_detector  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_ts(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def sqlite_ts(value: str) -> str:
    return parse_ts(value).strftime("%Y-%m-%d %H:%M:%S")


def load_registry_model(db_path: str, registry_id: int) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, device_id, model_type, trained_at, model_path, artifact_sha256
            FROM model_registry WHERE id = ?
            """,
            (registry_id,),
        ).fetchone()
    if row is None:
        raise SystemExit(f"model_registry id not found: {registry_id}")
    return dict(row)


def latest_registry_model(db_path: str, device_id: int, model_type: str) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, device_id, model_type, trained_at, model_path, artifact_sha256
            FROM model_registry
            WHERE device_id = ? AND model_type = ?
            ORDER BY active DESC, trained_at DESC, id DESC
            LIMIT 1
            """,
            (device_id, model_type),
        ).fetchone()
    if row is None:
        raise SystemExit(f"no model_registry entry for device={device_id} model_type={model_type}")
    return dict(row)


def load_flows(db_path: str, device_id: int, start: str, end: str) -> pd.DataFrame:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT device_id, timestamp, src_ip, dst_ip, src_port, dst_port,
                   protocol, bytes_sent, bytes_received, dns_query, flags
            FROM traffic_flows
            WHERE device_id = ?
              AND datetime(timestamp) >= datetime(?)
              AND datetime(timestamp) <= datetime(?)
            ORDER BY timestamp
            """,
            (device_id, sqlite_ts(start), sqlite_ts(end)),
        ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(row) for row in rows])
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="mixed")
    if "flags" in df.columns:
        df["flags"] = df["flags"].apply(lambda value: json.loads(value) if isinstance(value, str) and value else (value or {}))
        df["dns_rcode"] = df["flags"].apply(lambda value: value.get("dns_rcode") if isinstance(value, dict) else None)
        df["icmp_type"] = df["flags"].apply(lambda value: value.get("icmp_type") if isinstance(value, dict) else None)
        df["icmp_code"] = df["flags"].apply(lambda value: value.get("icmp_code") if isinstance(value, dict) else None)
    return df


def classify(label: str, detected: bool) -> str:
    positive = label.lower() in {"attack", "positive", "port_sweep", "attack_port_sweep"} or "attack" in label.lower()
    if positive and detected:
        return "TP"
    if positive and not detected:
        return "FN"
    if not positive and detected:
        return "FP"
    return "TN"


def is_positive_label(label: str) -> bool:
    return label.lower() in {"attack", "positive", "port_sweep", "attack_port_sweep"} or "attack" in label.lower()


def score_margin(score: float, threshold: float) -> float:
    return float(threshold) - float(score)


def detection_delay_seconds(start: str, first_detection) -> float | None:
    if first_detection is None:
        return None
    detected_at = pd.Timestamp(first_detection).to_pydatetime()
    return max(0.0, (detected_at - parse_ts(start)).total_seconds())


MODEL_TYPES = ["isolation_forest", "lof", "ocsvm", "autoencoder"]


def score_window(args: argparse.Namespace, model_type: str, model_file: str, model_info: dict | None) -> dict:
    flows = load_flows(args.database_path, args.device_id, args.start, args.end)
    extractor = FeatureExtractor(bucket_minutes=args.bucket_minutes)
    features = extractor.extract_features(flows) if not flows.empty else pd.DataFrame()
    if not features.empty:
        features = features[features["device_id"] == args.device_id]

    detector = get_detector(model_type, model_path=args.model_root)
    if not detector.load_model_file(str(model_file)):
        raise SystemExit(f"failed to load model artifact: {model_file}")
    scores = detector.score(features)
    threshold = float(getattr(detector, "threshold", 0.0))
    norm_threshold = float(detector.normalize_threshold())
    for row in scores:
        raw_score = float(row.get("anomaly_score") or 0.0)
        norm_score = float(detector.normalize_score(raw_score))
        row["threshold"] = threshold
        row["norm_score"] = norm_score
        row["norm_threshold"] = norm_threshold
        row["score_margin"] = score_margin(norm_score, norm_threshold)
        row["would_alert"] = bool(row.get("is_anomaly"))
    detected = any(bool(row.get("is_anomaly")) for row in scores)
    first_detection = next((row.get("bucket_start") for row in scores if row.get("is_anomaly")), None)
    classification = classify(args.label, detected)
    bucket_count = int(len(features))
    anomaly_buckets = sum(1 for row in scores if row.get("is_anomaly"))
    return {
        "requested_model_type": args.model_type,
        "effective_model_type": model_type,
        "model_type": model_type,
        "model_file": str(model_file),
        "model_registry_id": model_info.get("id") if model_info else None,
        "model_trained_at": model_info.get("trained_at") if model_info else None,
        "model_artifact_sha256": model_info.get("artifact_sha256") if model_info else None,
        "model_registry": model_info,
        "flow_count": int(len(flows)),
        "bucket_count": bucket_count,
        "detected": detected,
        "classification": classification,
        "first_detection_bucket": first_detection.isoformat(sep=" ") if first_detection is not None else None,
        "detection_delay_seconds": detection_delay_seconds(args.start, first_detection),
        "anomaly_buckets": anomaly_buckets,
        "anomaly_bucket_rate": round(anomaly_buckets / max(bucket_count, 1), 6),
        "false_positive_rate": round(anomaly_buckets / max(bucket_count, 1), 6) if not is_positive_label(args.label) else 0.0,
        "peak_anomaly_score": max((float(row.get("anomaly_score") or 0.0) for row in scores), default=0.0),
        "min_anomaly_score": min((float(row.get("anomaly_score") or 0.0) for row in scores), default=0.0),
        "max_score_margin": max((float(row.get("score_margin") or 0.0) for row in scores), default=0.0),
        "scores": scores,
    }


def load_windows(path: str) -> list[dict]:
    """Load labeled research windows from CSV or JSONL."""
    window_path = Path(path)
    if window_path.suffix.lower() == ".jsonl":
        with window_path.open("r", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    with window_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def model_rankings(results: list[dict]) -> list[dict]:
    by_model: dict[str, dict] = {}
    for result in results:
        item = by_model.setdefault(result["model_type"], {
            "model_type": result["model_type"],
            "tp": 0,
            "fp": 0,
            "tn": 0,
            "fn": 0,
            "windows": 0,
            "false_positive_buckets": 0,
            "benign_buckets": 0,
            "detection_delays": [],
        })
        item["windows"] += 1
        item[result["classification"].lower()] += 1
        if not is_positive_label(result["label"]):
            item["false_positive_buckets"] += int(result["anomaly_buckets"])
            item["benign_buckets"] += int(result["bucket_count"])
        if result.get("detection_delay_seconds") is not None:
            item["detection_delays"].append(float(result["detection_delay_seconds"]))
    rankings = []
    for item in by_model.values():
        precision = item["tp"] / max(item["tp"] + item["fp"], 1)
        recall = item["tp"] / max(item["tp"] + item["fn"], 1)
        f1 = 2 * precision * recall / max(precision + recall, 1e-9)
        delays = item.pop("detection_delays")
        rankings.append({
            **item,
            "precision": round(precision, 6),
            "recall": round(recall, 6),
            "f1": round(f1, 6),
            "false_positive_rate": round(item["false_positive_buckets"] / max(item["benign_buckets"], 1), 6),
            "mean_detection_delay_seconds": round(sum(delays) / len(delays), 3) if delays else None,
        })
    return sorted(rankings, key=lambda row: (-row["f1"], row["false_positive_rate"], row["mean_detection_delay_seconds"] or 0.0))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score historical flow windows with a current or archived model artifact.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--device-id", type=int, required=False)
    parser.add_argument("--model-type", choices=MODEL_TYPES, help="model type; inferred from --model-registry-id when omitted")
    parser.add_argument("--start", help="window start timestamp")
    parser.add_argument("--end", help="window end timestamp")
    parser.add_argument("--label", default="unknown", help="ground-truth label: benign or attack_port_sweep")
    parser.add_argument("--scenario", default="manual", help="research scenario name for a single window")
    parser.add_argument("--windows-file", help="CSV/JSONL with device_id,start,end,label,scenario research windows")
    parser.add_argument("--database-path", default=os.getenv("DATABASE_PATH", "/data/iot-security.db"))
    parser.add_argument("--model-root", default=os.getenv("MODEL_PATH", "/data/models"))
    parser.add_argument("--model-file", help="explicit .joblib artifact path")
    parser.add_argument("--model-registry-id", type=int, help="model_registry id to load")
    parser.add_argument("--compare-all", action="store_true", help="score the same window with latest registry artifact for all model types")
    parser.add_argument("--bucket-minutes", type=int, default=int(os.getenv("FEATURE_BUCKET_MINUTES", "5")))
    parser.add_argument("--run-id", help="stable output run id")
    parser.add_argument("--out-dir", default="artifacts/backtests")
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = Path(args.out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    runs = []

    windows = load_windows(args.windows_file) if args.windows_file else [{
        "device_id": args.device_id,
        "start": args.start,
        "end": args.end,
        "label": args.label,
        "scenario": args.scenario,
    }]

    for window in windows:
        if not window.get("device_id") or not window.get("start") or not window.get("end"):
            raise SystemExit("each research window requires device_id,start,end")
        window_args = argparse.Namespace(**vars(args))
        window_args.device_id = int(window["device_id"])
        window_args.start = window["start"]
        window_args.end = window["end"]
        window_args.label = window.get("label") or "unknown"
        window_args.scenario = window.get("scenario") or "unknown"
        if args.compare_all:
            for model_type in MODEL_TYPES:
                model_info = latest_registry_model(args.database_path, window_args.device_id, model_type)
                run = score_window(window_args, model_type, model_info["model_path"], model_info)
                run["label"] = window_args.label
                run["scenario"] = window_args.scenario
                runs.append(run)
            continue

        model_info = None
        model_file = args.model_file
        model_type = args.model_type
        if args.model_registry_id:
            model_info = load_registry_model(args.database_path, args.model_registry_id)
            model_file = model_info["model_path"]
            model_type = model_info["model_type"]
        elif not model_file:
            model_type = model_type or "isolation_forest"
            model_info = latest_registry_model(args.database_path, window_args.device_id, model_type)
            model_file = model_info["model_path"]
        if not model_type:
            raise SystemExit("--model-type is required when using --model-file without --model-registry-id")
        run = score_window(window_args, model_type, str(model_file), model_info)
        run["label"] = window_args.label
        run["scenario"] = window_args.scenario
        runs.append(run)

    for run in runs:
        suffix = run["model_type"] if args.compare_all else "scores"
        scenario = str(run.get("scenario", "manual")).replace("/", "-")
        output_name = f"scores-{scenario}-{suffix}.jsonl" if args.compare_all or args.windows_file else "scores.jsonl"
        with (run_dir / output_name).open("w", encoding="utf-8") as handle:
            for row in run.pop("scores"):
                payload = dict(row)
                if payload.get("bucket_start") is not None:
                    payload["bucket_start"] = payload["bucket_start"].isoformat(sep=" ")
                handle.write(json.dumps(payload, sort_keys=True) + "\n")

    summary = {
        "run_id": run_id,
        "created_at": utc_now(),
        "device_id": args.device_id,
        "window_start": sqlite_ts(args.start) if args.start else None,
        "window_end": sqlite_ts(args.end) if args.end else None,
        "label": args.label,
        "windows_file": args.windows_file,
        "compare_all": args.compare_all,
        "model_rankings": model_rankings(runs),
        "results": runs,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
