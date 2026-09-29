from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings


def _database_url() -> str:
    url = settings.database_url.strip()
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


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
