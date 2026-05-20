#!/usr/bin/env python3
"""Offline model backtesting for FP/FN research windows.

This script loads historical traffic_flows for one device, loads either a
versioned model_registry artifact or an explicit .joblib, and scores the window
without modifying live device risk tables.
"""

from __future__ import annotations

import argparse
import asyncio
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Score historical flow windows with a current or archived model artifact.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--device-id", type=int, required=True)
    parser.add_argument("--model-type", default="isolation_forest", choices=["isolation_forest", "lof", "ocsvm", "autoencoder"])
    parser.add_argument("--start", required=True, help="window start timestamp")
    parser.add_argument("--end", required=True, help="window end timestamp")
    parser.add_argument("--label", default="unknown", help="ground-truth label: benign or attack_port_sweep")
    parser.add_argument("--database-path", default=os.getenv("DATABASE_PATH", "/data/iot-security.db"))
    parser.add_argument("--model-root", default=os.getenv("MODEL_PATH", "/data/models"))
    parser.add_argument("--model-file", help="explicit .joblib artifact path")
    parser.add_argument("--model-registry-id", type=int, help="model_registry id to load")
    parser.add_argument("--bucket-minutes", type=int, default=int(os.getenv("FEATURE_BUCKET_MINUTES", "5")))
    parser.add_argument("--run-id", help="stable output run id")
    parser.add_argument("--out-dir", default="artifacts/backtests")
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_id = args.run_id or f"{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    run_dir = Path(args.out_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    model_info = None
    model_file = args.model_file
    if args.model_registry_id:
        model_info = load_registry_model(args.database_path, args.model_registry_id)
        model_file = model_info["model_path"]
    elif not model_file:
        model_info = latest_registry_model(args.database_path, args.device_id, args.model_type)
        model_file = model_info["model_path"]

    flows = load_flows(args.database_path, args.device_id, args.start, args.end)
    extractor = FeatureExtractor(bucket_minutes=args.bucket_minutes)
    features = extractor.extract_features(flows) if not flows.empty else pd.DataFrame()
    if not features.empty:
        features = features[features["device_id"] == args.device_id]

    detector = get_detector(args.model_type, model_path=args.model_root)
    if not detector.load_model_file(str(model_file)):
        raise SystemExit(f"failed to load model artifact: {model_file}")
    scores = detector.score(features)
    detected = any(bool(row.get("is_anomaly")) for row in scores)
    first_detection = next((row.get("bucket_start") for row in scores if row.get("is_anomaly")), None)
    classification = classify(args.label, detected)

    with (run_dir / "scores.jsonl").open("w", encoding="utf-8") as handle:
        for row in scores:
            payload = dict(row)
            if payload.get("bucket_start") is not None:
                payload["bucket_start"] = payload["bucket_start"].isoformat(sep=" ")
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    summary = {
        "run_id": run_id,
        "created_at": utc_now(),
        "device_id": args.device_id,
        "model_type": args.model_type,
        "model_file": str(model_file),
        "model_registry": model_info,
        "window_start": sqlite_ts(args.start),
        "window_end": sqlite_ts(args.end),
        "label": args.label,
        "flow_count": int(len(flows)),
        "bucket_count": int(len(features)),
        "detected": detected,
        "classification": classification,
        "first_detection_bucket": first_detection.isoformat(sep=" ") if first_detection is not None else None,
        "anomaly_buckets": sum(1 for row in scores if row.get("is_anomaly")),
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
