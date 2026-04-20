"""Training configuration and Train Now endpoints (Faza 3 + Faza 1).

Manages global and per-device training parameters that the ml-trainer
CronJob (and Train Now Jobs) read at training time.

Train Now creates a one-shot K8s Job that runs the ml-pipeline trainer
for a specific device + model_type.  The gateway-api pod needs the
``gateway-api`` ServiceAccount with a Role granting batch/jobs CRUD
(see k8s/gateway/ml-train-rbac.yaml).

Tables: global_training_config (single-row), device_training_config (per-device).
Both are created by ml_core.ensure_schema() at ml-pipeline startup.
The gateway-api creates them lazily on first write to be safe.
"""

from __future__ import annotations

import os
import re
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from ..core.database import get_db
from ..core.logging import log
from ..models.schemas_pydantic import (
    TrainingConfigResponse,
    TrainingConfigUpdate,
    DeviceTrainingConfigResponse,
    DeviceTrainingConfigUpdate,
)
from ..core.cache import cache

_k8s_logger = logging.getLogger("training.k8s")

router = APIRouter(prefix="/ml", tags=["training"])

# Default values matching ml_core.DEFAULT_TRAINING_CONFIG
_DEFAULTS = {
    "training_hours": 48,
    "min_training_samples": 10,
    "contamination": 0.05,
    "n_estimators": 200,
    "feature_bucket_minutes": 5,
    "per_device_models": True,
}


async def _ensure_tables(db: AsyncSession):
    """Lazily create training config tables if they don't exist."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS global_training_config (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            training_hours INTEGER NOT NULL DEFAULT 48,
            min_training_samples INTEGER NOT NULL DEFAULT 10,
            contamination REAL NOT NULL DEFAULT 0.05,
            n_estimators INTEGER NOT NULL DEFAULT 200,
            feature_bucket_minutes INTEGER NOT NULL DEFAULT 5,
            per_device_models INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    await db.execute(text("INSERT OR IGNORE INTO global_training_config (id) VALUES (1)"))
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS device_training_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id INTEGER NOT NULL UNIQUE,
            training_hours INTEGER,
            min_training_samples INTEGER,
            contamination REAL,
            n_estimators INTEGER,
            feature_bucket_minutes INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    await db.commit()


# ── Global config ────────────────────────────────────────────────────────────

@router.get("/config", response_model=TrainingConfigResponse)
async def get_global_config(db: AsyncSession = Depends(get_db)):
    """Return the cluster-wide training defaults."""
    await _ensure_tables(db)
    row = (await db.execute(text(
        "SELECT training_hours, min_training_samples, contamination, "
        "n_estimators, feature_bucket_minutes, per_device_models, updated_at "
        "FROM global_training_config WHERE id = 1"
    ))).first()
    if row is None:
        return TrainingConfigResponse(**_DEFAULTS)
    return TrainingConfigResponse(
        training_hours=row[0],
        min_training_samples=row[1],
        contamination=row[2],
        n_estimators=row[3],
        feature_bucket_minutes=row[4],
        per_device_models=bool(row[5]),
        updated_at=str(row[6]) if row[6] else None,
    )


@router.put("/config", response_model=TrainingConfigResponse)
async def update_global_config(
    data: TrainingConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Update cluster-wide training defaults. Only non-null fields are changed."""
    await _ensure_tables(db)
    updates = data.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    if "per_device_models" in updates:
        updates["per_device_models"] = 1 if updates["per_device_models"] else 0
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    await db.execute(
        text(f"UPDATE global_training_config SET {set_clause}, updated_at = CURRENT_TIMESTAMP WHERE id = 1"),
        updates,
    )
    await db.commit()
    log.info("global_training_config_updated", fields=list(updates.keys()))
    return await get_global_config(db)


# ── Per-device config ────────────────────────────────────────────────────────

@router.get("/devices/{device_id}/training-config")
async def get_device_effective_config(
    device_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return effective (merged) training config for a device."""
    await _ensure_tables(db)
    # Global
    g = (await db.execute(text(
        "SELECT training_hours, min_training_samples, contamination, "
        "n_estimators, feature_bucket_minutes, per_device_models "
        "FROM global_training_config WHERE id = 1"
    ))).first()
    merged = dict(_DEFAULTS) if g is None else {
        "training_hours": g[0],
        "min_training_samples": g[1],
        "contamination": g[2],
        "n_estimators": g[3],
        "feature_bucket_minutes": g[4],
        "per_device_models": bool(g[5]),
    }
    # Per-device overrides
    d = (await db.execute(text(
        "SELECT training_hours, min_training_samples, contamination, "
        "n_estimators, feature_bucket_minutes, updated_at "
        "FROM device_training_config WHERE device_id = :did"
    ), {"did": device_id})).first()
    has_overrides = d is not None
    overrides = {}
    if d:
        field_names = ["training_hours", "min_training_samples", "contamination",
                       "n_estimators", "feature_bucket_minutes"]
        for i, name in enumerate(field_names):
            if d[i] is not None:
                merged[name] = d[i]
                overrides[name] = d[i]
    return {
        **merged,
        "device_id": device_id,
        "has_overrides": has_overrides,
        "overrides": overrides,
    }


@router.put("/devices/{device_id}/training-config")
async def set_device_training_config(
    device_id: int,
    data: DeviceTrainingConfigUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Set per-device training config overrides. NULL values clear the override."""
    await _ensure_tables(db)
    updates = data.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    cols = ["device_id"] + list(updates.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    on_conflict = ", ".join(f"{k} = excluded.{k}" for k in updates)
    params = {"device_id": device_id, **updates}
    await db.execute(
        text(
            f"INSERT INTO device_training_config ({', '.join(cols)}, updated_at) "
            f"VALUES ({placeholders}, CURRENT_TIMESTAMP) "
            f"ON CONFLICT(device_id) DO UPDATE SET {on_conflict}, updated_at = CURRENT_TIMESTAMP"
        ),
        params,
    )
    await db.commit()
    log.info("device_training_config_updated", device_id=device_id, fields=list(updates.keys()))
    return await get_device_effective_config(device_id, db)


@router.delete("/devices/{device_id}/training-config")
async def delete_device_training_config(
    device_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Remove all per-device overrides, reverting to global defaults."""
    await _ensure_tables(db)
    await db.execute(
        text("DELETE FROM device_training_config WHERE device_id = :did"),
        {"did": device_id},
    )
    await db.commit()
    log.info("device_training_config_deleted", device_id=device_id)
    return await get_device_effective_config(device_id, db)


# ── Training data view (Faza 2) ─────────────────────────────────────────────

@router.get("/devices/{device_id}/training-data")
async def get_device_training_data(
    device_id: int,
    hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
):
    """Return training data summary: flow count, feature bucket stats, latest buckets."""
    async def _fetch():
        # Flow count
        row = (await db.execute(text(
            "SELECT COUNT(*) FROM traffic_flows "
            "WHERE device_id = :did AND timestamp >= datetime('now', '-' || :h || ' hours')"
        ), {"did": device_id, "h": hours})).first()
        flow_count = row[0] if row else 0

        # Feature bucket stats (aggregated from inference history)
        hist_rows = (await db.execute(text(
            "SELECT bucket_start, features FROM device_inference_history "
            "WHERE device_id = :did AND timestamp >= datetime('now', '-' || :h || ' hours') "
            "ORDER BY timestamp DESC LIMIT 100"
        ), {"did": device_id, "h": hours})).fetchall()

        buckets = []
        for r in hist_rows:
            import json as _json
            features = {}
            try:
                features = _json.loads(r[1]) if r[1] else {}
            except Exception:
                pass
            buckets.append({
                "bucket_start": r[0],
                "total_bytes": features.get("total_bytes", 0),
                "packets": features.get("packets", 0),
                "unique_destinations": features.get("unique_destinations", 0),
                "unique_ports": features.get("unique_ports", 0),
                "dns_queries": features.get("dns_queries", 0),
                "packet_rate": features.get("packet_rate", 0),
            })

        # Model metadata
        meta_rows = (await db.execute(text(
            "SELECT model_type, trained_at, samples, threshold, score_mean, "
            "estimated_anomaly_rate FROM model_metadata "
            "WHERE device_id = :did ORDER BY timestamp DESC LIMIT 4"
        ), {"did": device_id})).fetchall()

        models = []
        for m in meta_rows:
            models.append({
                "model_type": m[0],
                "trained_at": m[1],
                "samples": m[2],
                "threshold": m[3],
                "score_mean": m[4],
                "estimated_anomaly_rate": m[5],
            })

        return {
            "device_id": device_id,
            "hours": hours,
            "flow_count": flow_count,
            "bucket_count": len(buckets),
            "buckets": buckets[:20],  # Latest 20
            "models": models,
        }

    return await cache.get_or_set(
        f"training-data:{device_id}:{hours}",
        10.0,
        _fetch,
    )


@router.get("/devices/{device_id}/raw-flows")
async def get_device_raw_flows(
    device_id: int,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    hours: int = Query(24, ge=1, le=720),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated raw traffic flows for a device."""
    offset = (page - 1) * limit

    # Total count
    count_row = (await db.execute(text(
        "SELECT COUNT(*) FROM traffic_flows "
        "WHERE device_id = :did AND timestamp >= datetime('now', '-' || :h || ' hours')"
    ), {"did": device_id, "h": hours})).first()
    total = count_row[0] if count_row else 0

    # Paginated rows
    rows = (await db.execute(text(
        "SELECT id, timestamp, src_ip, dst_ip, src_port, dst_port, protocol, "
        "bytes_sent, bytes_received, packets, dns_query "
        "FROM traffic_flows "
        "WHERE device_id = :did AND timestamp >= datetime('now', '-' || :h || ' hours') "
        "ORDER BY timestamp DESC LIMIT :lim OFFSET :off"
    ), {"did": device_id, "h": hours, "lim": limit, "off": offset})).fetchall()

    flows = []
    for r in rows:
        flows.append({
            "id": r[0],
            "timestamp": r[1],
            "src_ip": r[2],
            "dst_ip": r[3],
            "src_port": r[4],
            "dst_port": r[5],
            "protocol": r[6],
            "bytes_sent": r[7],
            "bytes_received": r[8],
            "packets": r[9],
            "dns_query": r[10],
        })

    return {
        "device_id": device_id,
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": (total + limit - 1) // limit if limit > 0 else 0,
        "flows": flows,
    }


# ── Train Now (Faza 1) ─────────────────────────────────────────────────────

# K8s Job constants — mirror ml-trainer-cronjob.yaml.
_ML_IMAGE = os.getenv("ML_PIPELINE_IMAGE", "ghcr.io/kacperjarocki/ml-pipeline:latest")
_NAMESPACE = os.getenv("K8S_NAMESPACE", "iot-security")
_JOB_TTL = 300  # seconds after finished before auto-cleanup

# Valid model types (keep in sync with ml_core).
_VALID_MODELS = {"isolation_forest", "lof", "ocsvm", "autoencoder"}


def _sanitize_job_name(device_id: int, model_type: str) -> str:
    """Return a DNS-safe K8s Job name."""
    safe_model = model_type.replace("_", "-")
    return f"ml-train-{device_id}-{safe_model}"


def _build_job_manifest(device_id: int, model_type: str) -> dict:
    """Build a K8s batch/v1 Job dict for a single-device training run."""
    name = _sanitize_job_name(device_id, model_type)
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": _NAMESPACE,
            "labels": {
                "app": "ml-pipeline",
                "component": "train-now",
                "device-id": str(device_id),
                "model-type": model_type,
            },
        },
        "spec": {
            "ttlSecondsAfterFinished": _JOB_TTL,
            "backoffLimit": 1,
            "template": {
                "metadata": {
                    "labels": {
                        "app": "ml-pipeline",
                        "component": "train-now",
                    },
                },
                "spec": {
                    "nodeSelector": {
                        "node-role.kubernetes.io/gateway": "true",
                    },
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "ml-trainer",
                            "image": _ML_IMAGE,
                            "imagePullPolicy": "Always",
                            "command": ["python", "-m", "app.train"],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "256Mi"},
                                "limits": {"cpu": "500m", "memory": "512Mi"},
                            },
                            "env": [
                                {"name": "DATABASE_PATH", "value": "/data/iot-security.db"},
                                {"name": "MODEL_PATH", "value": "/data/models"},
                                {"name": "DEVICE_ID", "value": str(device_id)},
                                {"name": "MODEL_TYPE", "value": model_type},
                                {"name": "PER_DEVICE_MODELS", "value": "true"},
                                {"name": "LOG_LEVEL", "value": "info"},
                            ],
                            "volumeMounts": [
                                {"name": "sqlite-data", "mountPath": "/data"},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "sqlite-data",
                            "persistentVolumeClaim": {"claimName": "iot-security-sqlite"},
                        },
                        {
                            "name": "tmp",
                            "emptyDir": {"medium": "Memory", "sizeLimit": "100Mi"},
                        },
                    ],
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 1000,
                        "fsGroup": 1000,
                    },
                },
            },
        },
    }


def _get_k8s_batch_api():
    """Lazily initialise and return the K8s BatchV1Api client.

    Uses in-cluster config when running inside K8s, falls back to
    kubeconfig for local development.
    """
    from kubernetes import client, config as k8s_config  # noqa: deferred import

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    return client.BatchV1Api()


@router.post("/devices/{device_id}/train")
async def train_now(
    device_id: int,
    model_type: str = Query("isolation_forest"),
    db: AsyncSession = Depends(get_db),
):
    """Spawn a K8s Job that trains a single model for a single device.

    Returns the Job name and namespace so the caller can poll status via
    ``GET /ml/devices/{device_id}/train/status``.
    """
    if model_type not in _VALID_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid model_type '{model_type}'. Must be one of: {', '.join(sorted(_VALID_MODELS))}",
        )

    # Verify device exists.
    row = (await db.execute(
        text("SELECT id FROM devices WHERE id = :did"), {"did": device_id}
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Device {device_id} not found")

    job_name = _sanitize_job_name(device_id, model_type)
    batch_api = _get_k8s_batch_api()

    # Delete previous Job with the same name if it still lingers.
    try:
        batch_api.delete_namespaced_job(
            name=job_name,
            namespace=_NAMESPACE,
            propagation_policy="Background",
        )
        _k8s_logger.info("Deleted lingering job %s before re-creation", job_name)
    except Exception:
        pass  # Job doesn't exist — that's fine.

    manifest = _build_job_manifest(device_id, model_type)
    try:
        batch_api.create_namespaced_job(namespace=_NAMESPACE, body=manifest)
    except Exception as exc:
        log.error("train_now_job_create_failed", device_id=device_id, model_type=model_type, error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to create training Job: {exc}")

    log.info("train_now_job_created", device_id=device_id, model_type=model_type, job=job_name)
    return {
        "job_name": job_name,
        "namespace": _NAMESPACE,
        "device_id": device_id,
        "model_type": model_type,
        "status": "created",
    }


@router.get("/devices/{device_id}/train/status")
async def train_status(
    device_id: int,
    model_type: str = Query("isolation_forest"),
):
    """Poll the status of a Train Now K8s Job.

    Returns ``status`` as one of: ``running``, ``succeeded``, ``failed``,
    ``not_found``.
    """
    if model_type not in _VALID_MODELS:
        raise HTTPException(status_code=400, detail=f"Invalid model_type '{model_type}'")

    job_name = _sanitize_job_name(device_id, model_type)
    batch_api = _get_k8s_batch_api()

    try:
        job = batch_api.read_namespaced_job(name=job_name, namespace=_NAMESPACE)
    except Exception:
        return {"job_name": job_name, "status": "not_found", "device_id": device_id, "model_type": model_type}

    status = job.status
    if status.succeeded and status.succeeded > 0:
        state = "succeeded"
    elif status.failed and status.failed > 0:
        state = "failed"
    elif status.active and status.active > 0:
        state = "running"
    else:
        state = "pending"

    return {
        "job_name": job_name,
        "status": state,
        "device_id": device_id,
        "model_type": model_type,
        "start_time": str(status.start_time) if status.start_time else None,
        "completion_time": str(status.completion_time) if status.completion_time else None,
    }
