import os
import structlog
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager

from .models import WifiConfig, ValidationResult, GatewayStatus, ApplyResult
from .validate import validate_config
from .status import get_status
from .state import GatewayRuntime

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
)
log = structlog.get_logger()

def _bool_env(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.lower() == "true"


runtime = GatewayRuntime()


@asynccontextmanager
async def lifespan(app: FastAPI):
    apply_enabled = _bool_env("ENABLE_APPLY", False)
    auto_restore = _bool_env("AUTO_RESTORE", True)

    if apply_enabled and auto_restore:
        # Best-effort restore of last-known-good after start.
        try:
            ok, msg = await runtime.restore_from_disk()
            log.info("startup_restore", ok=ok, message=msg)
        except Exception as e:
            log.error("startup_restore_error", error=str(e))

    yield


app = FastAPI(title="Gateway Agent", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/status", response_model=GatewayStatus)
async def status(
    ap_interface: str = "wlan0",
    upstream_interface: str = "eth0",
):
    base = get_status(ap_interface=ap_interface, upstream_interface=upstream_interface)
    ps = runtime.process_status()
    ok, msg = runtime.read_last_apply()
    base.update(
        {
            "hostapd": ps.get("hostapd"),
            "dnsmasq": ps.get("dnsmasq"),
            "last_apply_ok": ok,
            "last_apply_message": msg,
            "apply_enabled": _bool_env("ENABLE_APPLY", False),
            "auto_restore": _bool_env("AUTO_RESTORE", True),
        }
    )
    return base


@app.post("/validate", response_model=ValidationResult)
async def validate(cfg: WifiConfig):
    return validate_config(cfg)


@app.post("/apply", response_model=ApplyResult)
async def apply(cfg: WifiConfig):
    # Safety: default OFF. This prevents bricking the k8s node by mistake.
    if os.getenv("ENABLE_APPLY", "false").lower() != "true":
        raise HTTPException(status_code=403, detail="apply disabled (set ENABLE_APPLY=true)")

    res = validate_config(cfg)
    if not res.ok:
        raise HTTPException(status_code=400, detail={"issues": res.issues})

    ok, message = await runtime.apply(cfg)
    return ApplyResult(ok=ok, message=message)


@app.post("/rollback", response_model=ApplyResult)
async def rollback():
    if os.getenv("ENABLE_APPLY", "false").lower() != "true":
        raise HTTPException(status_code=403, detail="rollback disabled (set ENABLE_APPLY=true)")
    ok, message = await runtime.rollback()
    return ApplyResult(ok=ok, message=message)
