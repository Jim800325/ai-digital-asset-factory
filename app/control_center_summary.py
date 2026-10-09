from __future__ import annotations

from sqlalchemy import text

from app.db import engine
from app.db_reliability import database_health
from app.live_acceptance_registry import live_acceptance_evidence_index
from app.migrate import migration_status


def build_control_center_summary() -> dict:
    db_state = database_health()
    base = {
        "status": "ok" if db_state["available"] else "degraded",
        "mode": "OBSERVE",
        "database": {
            "status": db_state.get("status"),
            "available": bool(db_state.get("available")),
        },
        "migrations": {
            "status": "DB_UNAVAILABLE",
            "expected_count": None,
            "applied_count": None,
        },
        "release_deployment": "DISABLED",
        "metrics": {
            "pipeline_runs": None,
            "opportunities": None,
            "human_reviews": None,
            "audit_records": None,
        },
        "recent_runs": [],
        "recent_opportunities": [],
    }

    if not db_state["available"]:
        return base

    migrations = migration_status()
    base["migrations"] = {
        "status": migrations.get("status"),
        "expected_count": migrations.get("expected_count"),
        "applied_count": migrations.get("applied_count"),
    }

    with engine.connect() as conn:
        base["metrics"]["pipeline_runs"] = conn.execute(
            text("SELECT count(*) FROM pipeline_runs")
        ).scalar_one()
        base["metrics"]["opportunities"] = conn.execute(
            text(
                """
                SELECT count(*)
                FROM digital_asset_opportunities
                WHERE COALESCE(title, '') NOT LIKE '[TEST_ONLY]%'
                """
            )
        ).scalar_one()
        base["metrics"]["human_reviews"] = conn.execute(
            text("SELECT count(*) FROM release_review_packages")
        ).scalar_one()

        run_rows = conn.execute(
            text(
                """
                SELECT id,status,evidence_created,started_at
                FROM pipeline_runs
                ORDER BY started_at DESC
                LIMIT 5
                """
            )
        ).mappings().all()
        base["recent_runs"] = [
            {
                "id": str(row["id"]),
                "status": row["status"],
                "evidence_created": row["evidence_created"],
                "started_at": row["started_at"],
            }
            for row in run_rows
        ]

        opportunity_rows = conn.execute(
            text(
                """
                SELECT title,canonical_title,score,independent_source_count,status
                FROM digital_asset_opportunities
                WHERE COALESCE(title, '') NOT LIKE '[TEST_ONLY]%'
                ORDER BY score DESC,created_at DESC
                LIMIT 5
                """
            )
        ).mappings().all()
        base["recent_opportunities"] = [
            {
                "title": row["title"] or row["canonical_title"] or "未命名机会",
                "score": row["score"],
                "independent_source_count": row["independent_source_count"],
                "status": row["status"],
            }
            for row in opportunity_rows
        ]

    audit_index = live_acceptance_evidence_index()
    base["metrics"]["audit_records"] = int(audit_index.get("record_count") or 0)
    return base
