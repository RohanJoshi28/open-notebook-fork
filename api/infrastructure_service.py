import asyncio
import os
from dataclasses import dataclass
from typing import Literal

import google.auth
from google.auth.transport.requests import AuthorizedSession, Request
from loguru import logger

# Minimal, dependency‑light Compute Engine helper built on google-auth only.
# We avoid google-api-python-client to keep image size small.

ComputeStatus = Literal[
    "PROVISIONING",
    "STAGING",
    "RUNNING",
    "STOPPING",
    "SUSPENDING",
    "SUSPENDED",
    "TERMINATED",
]


@dataclass(frozen=True)
class DbVmConfig:
    project: str
    zone: str
    name: str
    estimated_start_seconds: int = 90


_SESSION: AuthorizedSession | None = None
_CONFIG: DbVmConfig | None = None


def get_db_vm_config() -> DbVmConfig:
    """
    Resolve database VM identity from environment with sensible defaults.
    """
    global _CONFIG
    if _CONFIG:
        return _CONFIG

    # Environment overrides
    project = os.environ.get("DB_VM_PROJECT")
    zone = os.environ.get("DB_VM_ZONE", "us-central1-c")
    name = os.environ.get("DB_VM_NAME", "open-notebook-updated")

    # Fall back to application default credentials project if not provided
    if not project:
        credentials, default_project = google.auth.default()
        project = default_project
    else:
        credentials, _ = google.auth.default()

    if not project:
        raise RuntimeError("DB_VM_PROJECT is not set and default project could not be determined")

    _CONFIG = DbVmConfig(project=project, zone=zone, name=name)
    logger.info(
        "DB VM config resolved project=%s zone=%s name=%s", _CONFIG.project, _CONFIG.zone, _CONFIG.name
    )
    return _CONFIG


def _get_session() -> AuthorizedSession:
    global _SESSION
    if _SESSION is not None:
        return _SESSION

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/compute"])
    credentials.refresh(Request())
    _SESSION = AuthorizedSession(credentials)
    return _SESSION


async def _request(method: str, url: str) -> dict:
    """
    Run a synchronous AuthorizedSession request in a thread so we don't block the event loop.
    """
    session = _get_session()

    def _do():
        resp = session.request(method, url)
        return resp.status_code, resp.json() if resp.text else {}

    status_code, data = await asyncio.to_thread(_do)
    if status_code >= 400:
        error_msg = data.get("error", {}).get("message", f"HTTP {status_code}")
        raise RuntimeError(f"Compute API call failed: {error_msg}")
    return data


async def get_db_vm_status() -> ComputeStatus:
    cfg = get_db_vm_config()
    url = (
        f"https://compute.googleapis.com/compute/v1/projects/{cfg.project}"
        f"/zones/{cfg.zone}/instances/{cfg.name}"
    )
    data = await _request("GET", url)
    status = data.get("status", "UNKNOWN")
    logger.info("DB VM status=%s", status)
    return status  # type: ignore[return-value]


async def start_db_vm() -> dict:
    """
    Start or resume the DB VM. Returns the operation resource.
    """
    cfg = get_db_vm_config()
    current = await get_db_vm_status()

    if current == "RUNNING":
        logger.info("DB VM already running; start skipped")
        return {"status": current, "operation": None}

    action = "start"
    if current in {"SUSPENDED", "SUSPENDING"}:
        action = "resume"

    url = (
        f"https://compute.googleapis.com/compute/v1/projects/{cfg.project}"
        f"/zones/{cfg.zone}/instances/{cfg.name}/{action}"
    )
    logger.info("Issuing VM %s", action)
    data = await _request("POST", url)
    return {"status": current, "operation": data}


async def suspend_db_vm(prefer_suspend: bool = True) -> dict:
    """
    Suspend the DB VM. Falls back to stop if suspend is unsupported.
    """
    cfg = get_db_vm_config()
    status = await get_db_vm_status()
    if status in {"TERMINATED", "STOPPING"}:
        logger.info("DB VM already stopped/terminated; suspend skipped")
        return {"status": status, "operation": None}

    # Try suspend first
    if prefer_suspend:
        url = (
            f"https://compute.googleapis.com/compute/v1/projects/{cfg.project}"
            f"/zones/{cfg.zone}/instances/{cfg.name}/suspend"
        )
        try:
            logger.info("Issuing VM suspend")
            data = await _request("POST", url)
            return {"status": status, "operation": data, "action": "suspend"}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Suspend failed (%s); falling back to stop", exc)

    url = (
        f"https://compute.googleapis.com/compute/v1/projects/{cfg.project}"
        f"/zones/{cfg.zone}/instances/{cfg.name}/stop"
    )
    logger.info("Issuing VM stop")
    data = await _request("POST", url)
    return {"status": status, "operation": data, "action": "stop"}
