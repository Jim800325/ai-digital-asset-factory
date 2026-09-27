from pathlib import Path
from sqlalchemy import text
from app.db import engine

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

def migrate() -> None:
    with engine.begin() as db:
        db.execute(text("""
          CREATE TABLE IF NOT EXISTS schema_migrations(
            version text PRIMARY KEY,
            applied_at timestamptz NOT NULL DEFAULT now()
          )
        """))
    for path in sorted(MIGRATIONS.glob("*.sql")):
        version = path.name
        with engine.connect() as db:
            exists = db.execute(
                text("SELECT 1 FROM schema_migrations WHERE version=:v"), {"v": version}
            ).scalar()
        if exists:
            continue
        sql = path.read_text(encoding="utf-8")
        with engine.begin() as db:
            db.exec_driver_sql(sql)
            db.execute(text("INSERT INTO schema_migrations(version) VALUES(:v)"), {"v": version})
        print(f"migration applied: {version}", flush=True)

if __name__ == "__main__":
    migrate()
