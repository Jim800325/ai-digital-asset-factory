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
                    # DBAPI percent-style placeholders must not consume literal
                    # PostgreSQL PL/pgSQL syntax such as %ROWTYPE (migration 022).
                    # psycopg unescapes %% back to % before sending SQL.
                    db.exec_driver_sql(sql.replace("%", "%%"))
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
    pending = [version for version in expected if version not in applied_set]
    return {
        "status": "CURRENT" if not pending else "PENDING",
        "expected_count": len(expected),
        "applied_count": len(applied),
        "latest_version": applied[-1] if applied else None,
        "pending": pending,
    }


if __name__ == "__main__":
    migrate()
