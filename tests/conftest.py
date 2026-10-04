"""Deterministic database isolation for Shrimp Animation integration tests.

This fixture deliberately scopes cleanup to the Shrimp/production-provider test
surface. It does not change application behavior and it preserves
schema_migrations plus unrelated opportunity/release acceptance data used by
the wider integration suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from app.db import engine


_SHRIMP_TEST_PREFIX = "test_shrimp_animation"


def _is_shrimp_test(request: pytest.FixtureRequest) -> bool:
    path = Path(str(request.node.fspath))
    return path.name.startswith(_SHRIMP_TEST_PREFIX)


def _truncate_shrimp_test_surface() -> None:
    """Clear state that can leak between Shrimp integration tests.

    PostgreSQL resolves FK ordering for us through TRUNCATE ... CASCADE.  The
    table set is discovered from the migrated schema so new shrimp_* tables are
    automatically isolated without requiring fixture churn.
    """
    with engine.begin() as db:
        tables = [
            row[0]
            for row in db.execute(
                text(
                    """
                    SELECT tablename
                    FROM pg_tables
                    WHERE schemaname='public'
                      AND (
                        tablename LIKE 'shrimp_%'
                        OR tablename IN (
                          'production_provider_jobs',
                          'production_provider_definitions'
                        )
                      )
                    ORDER BY tablename
                    """
                )
            ).all()
        ]
        if not tables:
            return
        quoted = ",".join(
            '"' + name.replace('"', '""') + '"'
            for name in tables
        )
        db.exec_driver_sql(
            "TRUNCATE TABLE " + quoted + " RESTART IDENTITY CASCADE"
        )


@pytest.fixture(autouse=True)
def isolate_shrimp_animation_database(request: pytest.FixtureRequest):
    """Give every Shrimp test a deterministic database starting boundary."""
    if not _is_shrimp_test(request):
        yield
        return

    _truncate_shrimp_test_surface()
    yield
