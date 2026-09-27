from sqlalchemy import text
from app.db import engine

def enabled_sources() -> list[str]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT base_url FROM discovery_sources
          WHERE enabled=true ORDER BY priority DESC,id ASC
        """))
        return [r[0] for r in rows]
