import asyncio
import os
import time
import tomllib
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from loguru import logger

from open_notebook.database.repository import repo_query
from open_notebook.utils.version_utils import (
    compare_versions,
    get_version_from_github,
)
from api.infrastructure_service import is_db_vm_configured

router = APIRouter()

# In-memory cache for version check results
_version_cache: dict = {
    "latest_version": None,
    "has_update": False,
    "timestamp": 0,
    "check_failed": False,
}

# Cache TTL in seconds (24 hours)
VERSION_CACHE_TTL = 24 * 60 * 60


def get_version() -> str:
    """Read version from pyproject.toml"""
    try:
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
            return pyproject.get("project", {}).get("version", "unknown")
    except Exception as e:
        logger.warning(f"Could not read version from pyproject.toml: {e}")
        return "unknown"


def get_latest_version_cached(current_version: str) -> tuple[Optional[str], bool]:
    """
    Check for the latest version from GitHub with caching.

    Returns:
        tuple: (latest_version, has_update)
        - latest_version: str or None if check failed
        - has_update: bool indicating if update is available
    """
    global _version_cache

    # Check if cache is still valid (within TTL)
    cache_age = time.time() - _version_cache["timestamp"]
    if _version_cache["timestamp"] > 0 and cache_age < VERSION_CACHE_TTL:
        logger.debug(f"Using cached version check result (age: {cache_age:.0f}s)")
        return _version_cache["latest_version"], _version_cache["has_update"]

    # Cache expired or not yet set
    if _version_cache["timestamp"] > 0:
        logger.info(f"Version cache expired (age: {cache_age:.0f}s), refreshing...")

    # Perform version check with strict error handling
    try:
        logger.info("Checking for latest version from GitHub...")

        # Fetch latest version from GitHub with 10-second timeout
        latest_version = get_version_from_github(
            "https://github.com/lfnovo/open-notebook",
            "main"
        )

        logger.info(f"Latest version from GitHub: {latest_version}, Current version: {current_version}")

        # Compare versions
        has_update = compare_versions(current_version, latest_version) < 0

        # Cache the result
        _version_cache["latest_version"] = latest_version
        _version_cache["has_update"] = has_update
        _version_cache["timestamp"] = time.time()
        _version_cache["check_failed"] = False

        logger.info(f"Version check complete. Update available: {has_update}")

        return latest_version, has_update

    except Exception as e:
        logger.warning(f"Version check failed: {e}")

        # Cache the failure to avoid repeated attempts
        _version_cache["latest_version"] = None
        _version_cache["has_update"] = False
        _version_cache["timestamp"] = time.time()
        _version_cache["check_failed"] = True

        return None, False


async def check_database_health() -> dict:
    """
    Check if database is reachable using a lightweight query.

    Returns:
        dict with 'status' ("online" | "offline") and optional 'error'
    """
    timeout_s = 3.0  # keep the endpoint fast for UI; Surreal should respond quickly to RETURN 1
    try:
        result = await asyncio.wait_for(repo_query("RETURN 1"), timeout=timeout_s)
        if result:
            return {"status": "online"}
        return {"status": "offline", "error": "Empty result"}
    except asyncio.TimeoutError:
        msg = f"Health check timed out after {timeout_s} seconds"
        logger.warning(msg)
        return {"status": "offline", "error": msg}
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        return {"status": "offline", "error": str(e)}


@router.get("/config")
async def get_config(request: Request):
    """
    Get frontend configuration.

    Returns version information and health status.
    Note: The frontend determines the API URL via its own runtime-config endpoint,
    so this endpoint no longer returns apiUrl.

    Also checks for version updates from GitHub (with caching and error handling).
    """
    # Get current version
    current_version = get_version()

    # Check for updates (with caching and error handling) — optionally skipped in dev
    skip_version_check = os.environ.get("SKIP_VERSION_CHECK") == "1" or os.environ.get("NODE_ENV") == "development"
    latest_version = None
    has_update = False

    if not skip_version_check:
        try:
            latest_version, has_update = get_latest_version_cached(current_version)
        except Exception as e:
            # Extra safety: ensure version check never breaks the config endpoint
            logger.error(f"Unexpected error during version check: {e}")

    # Check database health (skip in dev when explicitly requested)
    skip_db_health = os.environ.get("SKIP_DB_HEALTH_CHECK") == "1" or os.environ.get("NODE_ENV") == "development"
    if skip_db_health:
        db_health = {"status": "online", "skipped": True}
        db_status = "online"
    else:
        db_health = await check_database_health()
        db_status = db_health["status"]
        if db_status == "offline":
            logger.warning(f"Database offline: {db_health.get('error', 'Unknown error')}")

    skip_db_vm_check = os.environ.get("SKIP_DB_VM_CHECK") == "1" or os.environ.get("NODE_ENV") == "development"
    db_vm_enabled = True
    if skip_db_vm_check:
        db_vm_enabled = False
    else:
        try:
            db_vm_enabled, _ = is_db_vm_configured()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"DB VM config check failed: {exc}")
            db_vm_enabled = False

    return {
        "version": current_version,
        "latestVersion": latest_version,
        "hasUpdate": has_update,
        "dbStatus": db_status,
        "dbVmEnabled": db_vm_enabled,
        "dbHealthSkipped": skip_db_health,
        "versionCheckSkipped": skip_version_check,
        "dbVmCheckSkipped": skip_db_vm_check,
    }
