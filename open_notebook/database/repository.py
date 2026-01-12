import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TypeVar, Union

from loguru import logger
from surrealdb import AsyncSurreal, RecordID  # type: ignore

T = TypeVar("T", Dict[str, Any], List[Dict[str, Any]])

# Reuse a single Surreal client to avoid repeated websocket handshakes (which can timeout on slow links)
_db_client: Optional[AsyncSurreal] = None
_db_initialized: bool = False


def get_database_url():
    """Get database URL with backward compatibility"""
    surreal_url = os.getenv("SURREAL_URL")
    if surreal_url:
        return surreal_url

    # Fallback to old format - WebSocket URL format
    address = os.getenv("SURREAL_ADDRESS", "localhost")
    port = os.getenv("SURREAL_PORT", "8000")
    return f"ws://{address}/rpc:{port}"


def get_database_password():
    """Get password with backward compatibility"""
    return os.getenv("SURREAL_PASSWORD") or os.getenv("SURREAL_PASS")


async def _get_client() -> AsyncSurreal:
    """
    Get (and lazily initialize) a shared AsyncSurreal client.
    Keeping a single connection dramatically reduces websocket handshake failures/timeouts.
    """
    global _db_client, _db_initialized
    if _db_client is None:
        _db_client = AsyncSurreal(get_database_url())

    if not _db_initialized:
        await _db_client.signin(
            {
                "username": os.environ.get("SURREAL_USER"),
                "password": get_database_password(),
            }
        )
        await _db_client.use(
            os.environ.get("SURREAL_NAMESPACE"), os.environ.get("SURREAL_DATABASE")
        )
        _db_initialized = True

    return _db_client


def parse_record_ids(obj: Any) -> Any:
    """Recursively parse and convert RecordIDs into strings."""
    if isinstance(obj, dict):
        return {k: parse_record_ids(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [parse_record_ids(item) for item in obj]
    elif isinstance(obj, RecordID):
        return str(obj)
    return obj


def ensure_record_id(value: Union[str, RecordID]) -> RecordID:
    """Ensure a value is a RecordID."""
    if isinstance(value, RecordID):
        return value
    return RecordID.parse(value)


def _coerce_owner(data: Dict[str, Any]) -> None:
    """Convert 'owner' string IDs to Surreal RecordID objects when present."""
    if "owner" in data and isinstance(data["owner"], str) and data["owner"]:
        try:
            data["owner"] = ensure_record_id(data["owner"])
        except Exception:
            # If parsing fails, leave as-is; Surreal will raise a clear error
            pass


@asynccontextmanager
async def db_connection():
    """
    Shared connection context. We don't close the connection to avoid repeated handshakes.
    """
    db = await _get_client()
    try:
        yield db
    finally:
        # Keep connection open for reuse
        pass


async def repo_query(
    query_str: str, vars: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Execute a SurrealQL query and return the results"""

    async with db_connection() as connection:
        start = time.time()
        try:
            result = parse_record_ids(await connection.query(query_str, vars))
            if isinstance(result, str):
                raise RuntimeError(result)
            duration = (time.time() - start) * 1000
            logger.debug(
                "DB OK %.2f ms | %s | params=%s | rows=%s",
                duration,
                query_str.replace("\n", " ")[:500],
                vars,
                len(result) if isinstance(result, list) else "n/a",
            )
            return result
        except RuntimeError as e:
            # RuntimeError is raised for retriable transaction conflicts - log without stack trace
            duration = (time.time() - start) * 1000
            logger.error("DB RETRYABLE %.2f ms | %s | params=%s | err=%s", duration, query_str[:200], vars, str(e))
            raise
        except Exception as e:
            # If the shared connection died, reset and retry once
            global _db_client, _db_initialized
            duration = (time.time() - start) * 1000
            logger.warning("DB ERROR %.2f ms (will reset) | %s | params=%s | err=%s", duration, query_str[:200], vars, str(e))
            _db_client = None
            _db_initialized = False
            try:
                start2 = time.time()
                conn = await _get_client()
                result = parse_record_ids(await conn.query(query_str, vars))
                if isinstance(result, str):
                    raise RuntimeError(result)
                duration2 = (time.time() - start2) * 1000
                logger.debug(
                    "DB OK after reset %.2f ms | %s | params=%s | rows=%s",
                    duration2,
                    query_str.replace("\n", " ")[:500],
                    vars,
                    len(result) if isinstance(result, list) else "n/a",
                )
                return result
            except Exception:
                logger.exception(e)
                raise


async def repo_create(table: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new record in the specified table"""
    # Remove 'id' attribute if it exists in data
    data.pop("id", None)
    data["created"] = datetime.now(timezone.utc)
    data["updated"] = datetime.now(timezone.utc)
    _coerce_owner(data)
    try:
        async with db_connection() as connection:
            return parse_record_ids(await connection.insert(table, data))
    except RuntimeError as e:
        logger.error(str(e))
        raise
    except Exception as e:
        logger.exception(e)
        raise RuntimeError("Failed to create record")


async def repo_relate(
    source: str, relationship: str, target: str, data: Optional[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Create a relationship between two records with optional data"""
    if data is None:
        data = {}
    query = f"RELATE {source}->{relationship}->{target} CONTENT $data;"
    # logger.debug(f"Relate query: {query}")

    return await repo_query(
        query,
        {
            "data": data,
        },
    )


async def repo_upsert(
    table: str, id: Optional[str], data: Dict[str, Any], add_timestamp: bool = False
) -> List[Dict[str, Any]]:
    """Create or update a record in the specified table"""
    data.pop("id", None)
    _coerce_owner(data)
    if add_timestamp:
        data["updated"] = datetime.now(timezone.utc)
    query = f"UPSERT {id if id else table} MERGE $data;"
    return await repo_query(query, {"data": data})


async def repo_update(
    table: str, id: str, data: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """Update an existing record by table and id"""
    # If id already contains the table name, use it as is
    try:
        if isinstance(id, RecordID) or (":" in id and id.startswith(f"{table}:")):
            record_id = id
        else:
            record_id = f"{table}:{id}"
        data.pop("id", None)
        _coerce_owner(data)
        if "created" in data and isinstance(data["created"], str):
            data["created"] = datetime.fromisoformat(data["created"])
        data["updated"] = datetime.now(timezone.utc)
        query = f"UPDATE {record_id} MERGE $data;"
        # logger.debug(f"Update query: {query}")
        result = await repo_query(query, {"data": data})
        # if isinstance(result, list):
        #     return [_return_data(item) for item in result]
        return parse_record_ids(result)
    except Exception as e:
        raise RuntimeError(f"Failed to update record: {str(e)}")


async def repo_delete(record_id: Union[str, RecordID]):
    """Delete a record by record id"""

    try:
        async with db_connection() as connection:
            return await connection.delete(ensure_record_id(record_id))
    except Exception as e:
        logger.exception(e)
        # Reset shared client and retry once on transaction conflicts
        msg = str(e)
        global _db_client, _db_initialized  # type: ignore
        _db_client = None
        _db_initialized = False
        if (
            "Failed to commit transaction" in msg
            or "read or write conflict" in msg
            or "can be retried" in msg
        ):
            try:
                async with db_connection() as connection:
                    return await connection.delete(ensure_record_id(record_id))
            except Exception as e2:
                logger.exception(e2)
                raise RuntimeError(f"Failed to delete record after retry: {str(e2)}")
        raise RuntimeError(f"Failed to delete record: {msg}")


async def repo_insert(
    table: str, data: List[Dict[str, Any]], ignore_duplicates: bool = False
) -> List[Dict[str, Any]]:
    """Create a new record in the specified table"""
    try:
        async with db_connection() as connection:
            return parse_record_ids(await connection.insert(table, data))
    except Exception as e:
        if ignore_duplicates and "already contains" in str(e):
            return []
        logger.exception(e)
        raise RuntimeError("Failed to create record")
