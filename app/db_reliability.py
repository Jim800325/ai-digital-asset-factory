import time
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from app.config import settings
from app.db import engine

T = TypeVar("T")


class DatabaseUnavailable(RuntimeError):
    def __init__(
        self,
        message: str = "Database is temporarily unavailable",
        *,
        operation: str = "database",
    ):
        super().__init__(message)
        self.operation = operation


def is_database_unavailable(exc: BaseException) -> bool:
    return isinstance(exc, (OperationalError, DBAPIError))


def read_with_retry(
    operation: str,
    fn: Callable[[], T],
) -> T:
    attempts = max(1, int(settings.database_read_retry_attempts))
    last: BaseException | None = None
    for index in range(attempts):
        try:
            return fn()
        except BaseException as exc:
            if not is_database_unavailable(exc):
                raise
            last = exc
            if index + 1 < attempts:
                time.sleep(0.08 * (index + 1))
    raise DatabaseUnavailable(operation=operation) from last


def database_health() -> dict[str, Any]:
    try:
        def probe():
            with engine.connect() as conn:
                conn.execute(text("select 1"))
        read_with_retry("health_probe", probe)
    except DatabaseUnavailable:
        return {
            "status": "DB_UNAVAILABLE",
            "available": False,
            "approval_mode": "APPROVAL_FAIL_CLOSED",
            "read_retry_attempts": max(
                1,
                int(settings.database_read_retry_attempts),
            ),
            "pooling": "NULL_POOL",
            "prepared_statements": "DISABLED",
        }
    return {
        "status": "AVAILABLE",
        "available": True,
        "approval_mode": "NORMAL",
        "read_retry_attempts": max(
            1,
            int(settings.database_read_retry_attempts),
        ),
        "pooling": "NULL_POOL",
        "prepared_statements": "DISABLED",
    }


def db_unavailable_payload(
    *,
    operation: str,
    approval_sensitive: bool = False,
) -> dict[str, Any]:
    return {
        "status": "DB_UNAVAILABLE",
        "database": "UNAVAILABLE",
        "operation": operation,
        "approval_mode": (
            "APPROVAL_FAIL_CLOSED"
            if approval_sensitive
            else "READ_DEGRADED"
        ),
        "retry_safe": not approval_sensitive,
        "message": (
            "Database connection is temporarily unavailable. "
            + (
                "Release approval remains fail-closed; the decision was not "
                "automatically retried."
                if approval_sensitive
                else "No write was attempted."
            )
        ),
    }
