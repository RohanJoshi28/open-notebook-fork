import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from loguru import logger

from api.infrastructure_service import (
    DbVmConfig,
    get_db_vm_config,
    get_db_vm_status,
    start_db_vm,
    suspend_db_vm,
)

router = APIRouter()


def _normalize_status(raw: str) -> str:
    raw_upper = (raw or "").upper()
    if raw_upper == "RUNNING":
        return "running"
    if raw_upper in {"SUSPENDING", "STOPPING"}:
        # Treat STOPPING the same as SUSPENDING so the UI shows a waiting state
        # while the Compute Engine operation completes.
        return "suspending"
    if raw_upper == "SUSPENDED":
        return "suspended"
    if raw_upper == "TERMINATED":
        return "stopped"
    if raw_upper in {"PROVISIONING", "STAGING"}:
        return "starting"
    return raw_upper.lower() or "unknown"


@router.get("/infra/db-vm/status")
async def db_vm_status():
    cfg: DbVmConfig = get_db_vm_config()
    try:
        status = await get_db_vm_status()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch VM status: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "status": _normalize_status(status),
        "rawStatus": status,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
        "config": {
            "project": cfg.project,
            "zone": cfg.zone,
            "name": cfg.name,
            "estimatedStartSeconds": cfg.estimated_start_seconds,
        },
    }


@router.post("/infra/db-vm/start")
async def db_vm_start():
    cfg: DbVmConfig = get_db_vm_config()
    try:
        result = await start_db_vm()
    except Exception as exc:  # noqa: BLE001
        logger.error("VM start failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "requestedAt": datetime.now(timezone.utc).isoformat(),
        "previousStatus": _normalize_status(result.get("status", "")),
        "operation": result.get("operation"),
        "config": {
            "project": cfg.project,
            "zone": cfg.zone,
            "name": cfg.name,
            "estimatedStartSeconds": cfg.estimated_start_seconds,
        },
    }


@router.post("/infra/db-vm/stop")
async def db_vm_stop():
    cfg: DbVmConfig = get_db_vm_config()
    try:
        result = await suspend_db_vm()
    except Exception as exc:  # noqa: BLE001
        logger.error("VM suspend/stop failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "requestedAt": datetime.now(timezone.utc).isoformat(),
        "previousStatus": _normalize_status(result.get("status", "")),
        "operation": result.get("operation"),
        "action": result.get("action", "suspend"),
        "config": {
            "project": cfg.project,
            "zone": cfg.zone,
            "name": cfg.name,
        },
    }
