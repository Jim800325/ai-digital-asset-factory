import os
import shlex

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings



def _canonical_connection_string(raw: str, *, source: str) -> tuple[str, bool]:
    value = (raw or "").strip()
    normalized = False

    if not value:
        raise RuntimeError(f"{source} is empty")

    if "\n" in value or "\r" in value:
        raise RuntimeError(
            f"{source} format is invalid: multiline values are not accepted; "
            "secret value was not logged"
        )

    assignment_prefix = source + "="
    if value.startswith(assignment_prefix):
        value = value[len(assignment_prefix):].strip()
        normalized = True

    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        value = value[1:-1].strip()
        normalized = True

    if value.startswith("psql "):
        try:
            parts = shlex.split(value)
        except ValueError as exc:
            raise RuntimeError(
                f"{source} format is invalid: malformed psql wrapper; "
                "secret value was not logged"
            ) from exc
        if len(parts) != 2 or parts[0] != "psql":
            raise RuntimeError(
                f"{source} format is invalid: only 'psql <url>' is accepted "
                "as a wrapper; secret value was not logged"
            )
        value = parts[1].strip()
        normalized = True

    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {"'", '"'}
    ):
        value = value[1:-1].strip()
        normalized = True

    allowed = (
        "postgresql://",
        "postgres://",
        "postgresql+psycopg://",
    )
    if not value.startswith(allowed):
        raise RuntimeError(
            f"{source} format is invalid: expected a PostgreSQL connection "
            "URL; secret value was not logged"
        )

    if any(ch.isspace() for ch in value):
        raise RuntimeError(
            f"{source} format is invalid: whitespace is not accepted inside "
            "the connection URL; secret value was not logged"
        )

    return value, normalized


def database_selection() -> dict[str, str | bool]:
    vercel_env = (os.getenv("VERCEL_ENV") or "").strip().lower()

    if (
        vercel_env == "production"
        and settings.deployment_authorization_preview_only
    ):
        raise RuntimeError(
            "Deployment Authorization v0.1 is preview-only and cannot start "
            "in Vercel production"
        )

    if vercel_env == "preview":
        preview_url = settings.preview_database_url.strip()
        if not preview_url:
            raise RuntimeError(
                "PREVIEW_DATABASE_URL is required for Vercel Preview; "
                "DATABASE_URL is intentionally not accepted"
            )
        canonical_url, normalized = _canonical_connection_string(
            preview_url,
            source="PREVIEW_DATABASE_URL",
        )
        return {
            "url": canonical_url,
            "source": "PREVIEW_DATABASE_URL",
            "vercel_env": "preview",
            "preview_isolated": True,
            "input_normalized": normalized,
        }

    canonical_url, normalized = _canonical_connection_string(
        settings.database_url.strip(),
        source="DATABASE_URL",
    )
    return {
        "url": canonical_url,
        "source": "DATABASE_URL",
        "vercel_env": vercel_env or "non-vercel",
        "preview_isolated": False,
        "input_normalized": normalized,
    }


def _normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def _database_url() -> str:
    return _normalize_database_url(str(database_selection()["url"]))


engine = create_engine(
    _database_url(),
    poolclass=NullPool,
    pool_pre_ping=False,
    connect_args={
        "connect_timeout": max(1, settings.database_connect_timeout_seconds),
        "application_name": settings.database_application_name,
        # Transaction poolers such as Neon/PgBouncer should not rely on
        # connection-scoped prepared statements across serverless invocations.
        "prepare_threshold": None,
    },
)

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
