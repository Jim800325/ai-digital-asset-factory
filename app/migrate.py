import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.db import engine

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_LOCK_KEY = 42690316016

PREVIEW_LEGACY_RECONCILIATIONS = {
    "043_shrimp_animation_youtube_live_acceptance.sql": {
        "reason": "legacy Preview-only YouTube live acceptance branch residue",
        "expected_object": "shrimp_animation_youtube_live_acceptance_runs",
    },
}


def migration_files() -> list[str]:
    return [path.name for path in sorted(MIGRATIONS.glob("*.sql"))]


def migrate() -> None:
    files = sorted(MIGRATIONS.glob("*.sql"))
    with engine.connect() as db:
        db.execute(
            text("SELECT pg_advisory_lock(:key)"),
            {"key": MIGRATION_LOCK_KEY},
        )
        db.commit()
        try:
            with db.begin():
                db.execute(text("""
                  CREATE TABLE IF NOT EXISTS schema_migrations(
                    version text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                  )
                """))

            for path in files:
                with db.begin():
                    exists = db.execute(
                        text(
                            "SELECT 1 FROM schema_migrations "
                            "WHERE version=:version"
                        ),
                        {"version": path.name},
                    ).scalar()
                    if exists:
                        continue

                    sql = path.read_text(encoding="utf-8")
                    db.exec_driver_sql(sql)
                    db.execute(
                        text(
                            "INSERT INTO schema_migrations(version) "
                            "VALUES(:version)"
                        ),
                        {"version": path.name},
                    )
                print(f"migration applied: {path.name}", flush=True)
        finally:
            db.execute(
                text("SELECT pg_advisory_unlock(:key)"),
                {"key": MIGRATION_LOCK_KEY},
            )
            db.commit()


def migration_status() -> dict:
    expected = migration_files()
    try:
        with engine.connect() as db:
            rows = db.execute(text("""
              SELECT version,applied_at
              FROM schema_migrations
              ORDER BY version
            """)).mappings().all()
    except ProgrammingError:
        return {
            "status": "NOT_INITIALIZED",
            "expected_count": len(expected),
            "applied_count": 0,
            "latest_version": None,
            "pending": expected,
        }

    applied = [str(row["version"]) for row in rows]
    applied_set = set(applied)
    expected_set = set(expected)
    pending = [version for version in expected if version not in applied_set]
    unexpected_rows = [
        row for row in rows if str(row["version"]) not in expected_set
    ]

    schema_objects = set()
    if unexpected_rows:
        with engine.connect() as diagnostics_db:
            schema_objects = {
                str(row["table_name"])
                for row in diagnostics_db.execute(text("""
                  SELECT table_name
                  FROM information_schema.tables
                  WHERE table_schema='public'
                """)).mappings().all()
            }

    is_preview = (os.getenv("VERCEL_ENV") or "").strip().lower() == "preview"
    reconciled_rows = []
    unresolved_rows = []
    for row in unexpected_rows:
        version = str(row["version"])
        policy = PREVIEW_LEGACY_RECONCILIATIONS.get(version)
        expected_object = policy.get("expected_object") if policy else None
        if (
            is_preview
            and policy is not None
            and expected_object in schema_objects
        ):
            reconciled_rows.append((row, policy))
        else:
            unresolved_rows.append(row)

    status = (
        "DRIFT"
        if unresolved_rows
        else ("CURRENT" if not pending else "PENDING")
    )
    applied_current_count = sum(
        1 for version in expected if version in applied_set
    )
    return {
        "status": status,
        "expected_count": len(expected),
        "applied_count": applied_current_count,
        "recorded_count": len(applied),
        "latest_version": (
            next(
                (version for version in reversed(expected) if version in applied_set),
                None,
            )
        ),
        "pending": pending,
        "unexpected": [str(row["version"]) for row in unresolved_rows],
        "reconciled_legacy": [
            {
                "version": str(row["version"]),
                "applied_at": (
                    row["applied_at"].isoformat()
                    if row["applied_at"] is not None
                    else None
                ),
                "reason": policy["reason"],
                "preserved_object": policy["expected_object"],
            }
            for row, policy in reconciled_rows
        ],
    }


if __name__ == "__main__":
    migrate()
