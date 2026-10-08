from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.db import engine

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
MIGRATION_LOCK_KEY = 42690316016


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
    unexpected = [str(row["version"]) for row in unexpected_rows]

    youtube_objects = []
    if unexpected:
        youtube_objects = [
            dict(row)
            for row in db.execute(text("""
              SELECT table_schema,table_name,table_type
              FROM information_schema.tables
              WHERE table_schema='public'
                AND lower(table_name) LIKE '%youtube%'
              ORDER BY table_name
            """)).mappings().all()
        ]

    status = "DRIFT" if unexpected else ("CURRENT" if not pending else "PENDING")
    return {
        "status": status,
        "expected_count": len(expected),
        "applied_count": len(applied),
        "latest_version": applied[-1] if applied else None,
        "pending": pending,
        "unexpected": unexpected,
        "unexpected_details": [
            {
                "version": str(row["version"]),
                "applied_at": (
                    row["applied_at"].isoformat()
                    if row["applied_at"] is not None
                    else None
                ),
            }
            for row in unexpected_rows
        ],
        "unexpected_schema_objects": youtube_objects,
    }


if __name__ == "__main__":
    migrate()
