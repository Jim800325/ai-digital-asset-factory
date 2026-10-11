from __future__ import annotations

from sqlalchemy import text

from app.db import engine
from app.db_reliability import DatabaseUnavailable, database_health, read_with_retry
from app.manual_pipeline import manual_pipeline_readiness
from app.migrate import migration_status
from app.workbench_actions import build_workbench_actions


_REAL_OPPORTUNITY = "COALESCE(o.title, '') NOT LIKE '[TEST_ONLY]%'"


def _empty_funnel() -> dict:
    return {
        "discovered": 0,
        "research": 0,
        "candidate": 0,
        "validating": 0,
        "build_ready": 0,
        "building": 0,
        "published": 0,
        "profitable": 0,
    }


def _next_action(row: dict) -> str:
    if row.get("build_readiness") == "BUILD_READY":
        if row.get("build_proposal_status") == "PENDING_APPROVAL":
            return "REVIEW_BUILD_PROPOSAL"
        return "BUILD_READY"
    if not bool(row.get("evidence_gate_passed")):
        return "EXPAND_EVIDENCE"
    if row.get("status") == "CANDIDATE" and float(row.get("research_validation_score") or 0) <= 0:
        return "RUN_RESEARCH_VALIDATION"
    return "REVIEW_OPPORTUNITY"


def _bottleneck(metrics: dict) -> dict:
    if int(metrics.get("evidence_blocked") or 0) > 0:
        count = int(metrics["evidence_blocked"])
        return {
            "stage": "EVIDENCE_GATE",
            "count": count,
            "reason": f"{count} 個機會仍缺少足夠的獨立來源或證據品質。",
            "next_action": "EVIDENCE_EXPANSION",
        }
    if int(metrics.get("candidate_validations_missing") or 0) > 0:
        count = int(metrics["candidate_validations_missing"])
        return {
            "stage": "RESEARCH_VALIDATION",
            "count": count,
            "reason": f"{count} 個 CANDIDATE 尚未完成商業驗證。",
            "next_action": "RESEARCH_VALIDATION",
        }
    if int(metrics.get("build_approval_pending") or 0) > 0:
        count = int(metrics["build_approval_pending"])
        return {
            "stage": "BUILD_APPROVAL",
            "count": count,
            "reason": f"{count} 個 Build Proposal 等待人工決策。",
            "next_action": "BUILD_APPROVAL",
        }
    return {
        "stage": "DISCOVERY",
        "count": 0,
        "reason": "目前沒有明確業務 Gate 阻塞。",
        "next_action": "PIPELINE_RUN",
    }


def build_workbench_summary() -> dict:
    db_state = database_health()
    pipeline = manual_pipeline_readiness()
    base = {
        "status": "ok" if db_state["available"] else "degraded",
        "product": "SIDE_BUSINESS_WORKBENCH",
        "mode": "OBSERVE",
        "funnel": _empty_funnel(),
        "stage_availability": {
            "discovered": "ACTIVE",
            "research": "ACTIVE",
            "candidate": "ACTIVE",
            "validating": "ACTIVE",
            "build_ready": "ACTIVE",
            "building": "ACTIVE",
            "published": "PLANNED",
            "profitable": "PLANNED",
        },
        "metrics": {
            "evidence_records": 0,
            "evidence_blocked": 0,
            "candidate_reports_missing": 0,
            "candidate_validations_missing": 0,
            "build_approval_pending": 0,
            "release_review_pending": 0,
        },
        "bottleneck": {
            "stage": "DATABASE",
            "count": 0,
            "reason": "Database unavailable; business funnel cannot be evaluated.",
            "next_action": "SYSTEM_CHECK",
        },
        "actions": [],
        "top_opportunities": [],
        "latest_run": None,
        "system": {
            "status": "READY" if db_state["available"] and pipeline["status"] == "READY" else "DEGRADED",
            "database": db_state.get("status"),
            "migrations": "DB_UNAVAILABLE",
            "pipeline": pipeline["status"],
            "worker_count": int(pipeline.get("active_worker_count") or 0),
        },
    }

    if not db_state["available"]:
        base["actions"] = [
            {
                "id": "system-check",
                "priority": "ACTION_REQUIRED",
                "type": "SYSTEM_CHECK",
                "title": "檢查系統資料層",
                "count": 1,
                "reason": "Database unavailable；副業漏斗已安全降級為唯讀不可用狀態。",
                "href": "/system",
            }
        ]
        return base

    migrations = migration_status()
    base["system"]["migrations"] = migrations.get("status")

    def _load() -> dict:
        with engine.connect() as conn:
            funnel_row = conn.execute(
                text(
                    f"""
                    SELECT
                      count(*) FILTER (WHERE {_REAL_OPPORTUNITY}) AS discovered,
                      count(*) FILTER (WHERE {_REAL_OPPORTUNITY} AND o.status='RESEARCH') AS research,
                      count(*) FILTER (WHERE {_REAL_OPPORTUNITY} AND o.status='CANDIDATE') AS candidate,
                      count(*) FILTER (
                        WHERE {_REAL_OPPORTUNITY}
                          AND EXISTS (
                            SELECT 1 FROM research_validations rv
                            WHERE rv.opportunity_id=o.id
                              AND rv.validation_status='CURRENT'
                          )
                          AND o.build_readiness<>'BUILD_READY'
                      ) AS validating,
                      count(*) FILTER (
                        WHERE {_REAL_OPPORTUNITY}
                          AND o.build_readiness='BUILD_READY'
                      ) AS build_ready
                    FROM digital_asset_opportunities o
                    """
                )
            ).mappings().one()

            building = conn.execute(
                text(
                    """
                    SELECT count(DISTINCT bp.opportunity_id)
                    FROM sandbox_build_requests sbr
                    JOIN build_proposals bp ON bp.id=sbr.proposal_id
                    JOIN digital_asset_opportunities o ON o.id=bp.opportunity_id
                    WHERE COALESCE(o.title,'') NOT LIKE '[TEST_ONLY]%'
                      AND sbr.request_status NOT IN ('FAILED','REJECTED','CANCELLED','FINISHED')
                    """
                )
            ).scalar_one()

            evidence_records = conn.execute(
                text(
                    """
                    SELECT count(DISTINCT oe.evidence_id)
                    FROM opportunity_evidence oe
                    JOIN digital_asset_opportunities o ON o.id=oe.opportunity_id
                    WHERE COALESCE(o.title,'') NOT LIKE '[TEST_ONLY]%'
                    """
                )
            ).scalar_one()

            metrics_row = conn.execute(
                text(
                    """
                    SELECT
                      count(*) FILTER (
                        WHERE COALESCE(o.title,'') NOT LIKE '[TEST_ONLY]%'
                          AND o.evidence_gate_passed=false
                      ) AS evidence_blocked,
                      count(*) FILTER (
                        WHERE COALESCE(o.title,'') NOT LIKE '[TEST_ONLY]%'
                          AND o.status='CANDIDATE'
                          AND NOT EXISTS (
                            SELECT 1 FROM research_reports rr
                            WHERE rr.opportunity_id=o.id
                              AND rr.report_status='GENERATED'
                          )
                      ) AS candidate_reports_missing,
                      count(*) FILTER (
                        WHERE COALESCE(o.title,'') NOT LIKE '[TEST_ONLY]%'
                          AND o.status='CANDIDATE'
                          AND NOT EXISTS (
                            SELECT 1 FROM research_validations rv
                            WHERE rv.opportunity_id=o.id
                              AND rv.validation_status='CURRENT'
                          )
                      ) AS candidate_validations_missing,
                      count(*) FILTER (
                        WHERE COALESCE(o.title,'') NOT LIKE '[TEST_ONLY]%'
                          AND o.build_proposal_status='PENDING_APPROVAL'
                      ) AS build_approval_pending
                    FROM digital_asset_opportunities o
                    """
                )
            ).mappings().one()

            release_review_pending = conn.execute(
                text(
                    """
                    SELECT count(*)
                    FROM release_candidates rc
                    JOIN build_proposals bp ON bp.id=rc.proposal_id
                    JOIN digital_asset_opportunities o ON o.id=bp.opportunity_id
                    WHERE COALESCE(o.title,'') NOT LIKE '[TEST_ONLY]%'
                      AND rc.release_status='READY_FOR_REVIEW'
                    """
                )
            ).scalar_one()

            latest_run = conn.execute(
                text(
                    """
                    SELECT id,status,pages_discovered,pages_crawled,evidence_created,
                           opportunities_created,started_at,finished_at
                    FROM pipeline_runs
                    ORDER BY started_at DESC
                    LIMIT 1
                    """
                )
            ).mappings().one_or_none()

            top_rows = conn.execute(
                text(
                    """
                    SELECT id,title,canonical_title,asset_type,score,status,
                           independent_source_count,evidence_count,evidence_quality_score,
                           source_diversity_score,signal_strength_score,evidence_gate_passed,
                           research_validation_score,build_readiness,build_proposal_status,
                           updated_at
                    FROM digital_asset_opportunities
                    WHERE COALESCE(title,'') NOT LIKE '[TEST_ONLY]%'
                    ORDER BY score DESC,updated_at DESC
                    LIMIT 10
                    """
                )
            ).mappings().all()

        return {
            "funnel": dict(funnel_row),
            "building": int(building or 0),
            "evidence_records": int(evidence_records or 0),
            "metrics": dict(metrics_row),
            "release_review_pending": int(release_review_pending or 0),
            "latest_run": dict(latest_run) if latest_run else None,
            "top_rows": [dict(row) for row in top_rows],
        }

    try:
        data = read_with_retry("workbench_summary", _load)
    except DatabaseUnavailable:
        base["status"] = "degraded"
        base["system"]["status"] = "DEGRADED"
        base["system"]["database"] = "DB_UNAVAILABLE"
        base["actions"] = [
            {
                "id": "system-check",
                "priority": "ACTION_REQUIRED",
                "type": "SYSTEM_CHECK",
                "title": "檢查系統資料層",
                "count": 1,
                "reason": "Database read failed；未執行任何寫入。",
                "href": "/system",
            }
        ]
        return base

    funnel = {
        "discovered": int(data["funnel"]["discovered"] or 0),
        "research": int(data["funnel"]["research"] or 0),
        "candidate": int(data["funnel"]["candidate"] or 0),
        "validating": int(data["funnel"]["validating"] or 0),
        "build_ready": int(data["funnel"]["build_ready"] or 0),
        "building": int(data["building"]),
        "published": 0,
        "profitable": 0,
    }
    metrics = {
        "evidence_records": int(data["evidence_records"]),
        "evidence_blocked": int(data["metrics"]["evidence_blocked"] or 0),
        "candidate_reports_missing": int(data["metrics"]["candidate_reports_missing"] or 0),
        "candidate_validations_missing": int(data["metrics"]["candidate_validations_missing"] or 0),
        "build_approval_pending": int(data["metrics"]["build_approval_pending"] or 0),
        "release_review_pending": int(data["release_review_pending"]),
        "candidate": funnel["candidate"],
    }

    top_opportunities = []
    for row in data["top_rows"]:
        top_opportunities.append(
            {
                "id": str(row["id"]),
                "title": row["title"] or row["canonical_title"] or "未命名機會",
                "asset_type": row["asset_type"],
                "score": row["score"],
                "stage": row["status"],
                "independent_source_count": row["independent_source_count"],
                "evidence_count": row["evidence_count"],
                "evidence_quality_score": row["evidence_quality_score"],
                "source_diversity_score": row["source_diversity_score"],
                "signal_strength_score": row["signal_strength_score"],
                "evidence_gate_passed": bool(row["evidence_gate_passed"]),
                "build_readiness": row["build_readiness"],
                "next_action": _next_action(row),
                "updated_at": row["updated_at"],
            }
        )

    latest = data["latest_run"]
    base["funnel"] = funnel
    base["metrics"] = {key: value for key, value in metrics.items() if key != "candidate"}
    base["bottleneck"] = _bottleneck(metrics)
    base["actions"] = build_workbench_actions(metrics)
    base["top_opportunities"] = top_opportunities
    base["latest_run"] = (
        {
            "id": str(latest["id"]),
            "status": latest["status"],
            "pages_discovered": latest["pages_discovered"],
            "pages_crawled": latest["pages_crawled"],
            "evidence_created": latest["evidence_created"],
            "opportunities_created": latest["opportunities_created"],
            "started_at": latest["started_at"],
            "finished_at": latest["finished_at"],
        }
        if latest
        else None
    )
    return base
