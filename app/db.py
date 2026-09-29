import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings


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
        return {
            "url": preview_url,
            "source": "PREVIEW_DATABASE_URL",
            "vercel_env": "preview",
            "preview_isolated": True,
        }

    return {
        "url": settings.database_url.strip(),
        "source": "DATABASE_URL",
        "vercel_env": vercel_env or "non-vercel",
        "preview_isolated": False,
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
