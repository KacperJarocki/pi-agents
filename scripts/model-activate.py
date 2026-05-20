#!/usr/bin/env python3
"""Activate an archived model_registry version as the current inference model."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from pathlib import Path


def current_model_path(model_root: str, device_id: int | None, model_type: str) -> str:
    if model_type == "isolation_forest":
        name = f"isolation_forest_model_device_{device_id}.joblib" if device_id is not None else "isolation_forest_model.joblib"
    else:
        name = f"{model_type}_model_device_{device_id}.joblib" if device_id is not None else f"{model_type}_model.joblib"
    return str(Path(model_root) / name)


def load_registry_row(db_path: str, registry_id: int) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT id, device_id, model_type, trained_at, model_path, current_path, artifact_sha256
            FROM model_registry WHERE id = ?
            """,
            (registry_id,),
        ).fetchone()
    if row is None:
        raise SystemExit(f"model_registry id not found: {registry_id}")
    return dict(row)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy an archived model artifact back to the active inference path.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--model-registry-id", type=int, required=True)
    parser.add_argument("--database-path", default=os.getenv("DATABASE_PATH", "/data/iot-security.db"))
    parser.add_argument("--model-root", default=os.getenv("MODEL_PATH", "/data/models"))
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    row = load_registry_row(args.database_path, args.model_registry_id)
    source = row["model_path"]
    target = row["current_path"] or current_model_path(args.model_root, row["device_id"], row["model_type"])
    if not source or not os.path.exists(source):
        raise SystemExit(f"archived model artifact missing: {source}")

    summary = {
        "model_registry_id": row["id"],
        "device_id": row["device_id"],
        "model_type": row["model_type"],
        "trained_at": row["trained_at"],
        "source": source,
        "target": target,
        "dry_run": args.dry_run,
    }
    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    Path(target).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    with sqlite3.connect(args.database_path) as conn:
        conn.execute(
            "UPDATE model_registry SET active = 0 WHERE device_id IS ? AND model_type = ?",
            (row["device_id"], row["model_type"]),
        )
        conn.execute("UPDATE model_registry SET active = 1, current_path = ? WHERE id = ?", (target, row["id"]))
        conn.commit()
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
