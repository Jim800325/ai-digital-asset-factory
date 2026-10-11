from __future__ import annotations

from collections import Counter
from uuid import UUID

from sqlalchemy import text

from app.db import engine
from app.db_reliability import DatabaseUnavailable, read_with_retry


REAL_OPPORTUNITY_SQL = "COALESCE(o.title, '') NOT LIKE '[TEST_ONLY]%'"

EVIDENCE_GATE_THRESHOLDS = {
    "independent_sources": 2,
    "evidence_quality": 65.0,
    "source_diversity": 60.0,
    "signal_strength": 55.0,
    "candidate_score": 75.0,
}

SORT_SQL = {
    "priority": (
        "CASE "
        "WHEN o.build_readiness='BUILD_READY' THEN 0 "
        "WHEN o.status='CANDIDATE' THEN 1 "
        "WHEN o.status='RESEARCH' THEN 2 "
        "WHEN o.status='WATCH' THEN 3 "
        "ELSE 4 END, "
        "o.evidence_gate_passed DESC, o.score DESC, o.updated_at DESC"
    ),
    "score": "o.score",
    "evidence_quality": "o.evidence_quality_score",
    "sources": "o.independent_source_count",
    "updated": "o.updated_at",
    "validation_score": "o.research_validation_score",
}

ALLOWED_STAGES = {"WATCH", "RESEARCH", "CANDIDATE"}
ALLOWED_ASSET_TYPES = {
    "DATASET_API",
    "INTELLIGENCE_REPORT",
    "MICRO_SAAS_TOOL",
    "TEMPLATE_WORKFLOW",
    "CONTENT_IP",
}
ALLOWED_GATE = {"PASS", "BLOCKED"}
ALLOWED_BUILD_READINESS = {"NOT_READY", "BUILD_READY"}


def _number(value) -> float:
    return round(float(value or 0), 2)


def _worth_attention(row: dict) -> list[dict]:
    dimensions = [
        ("DEMAND", "需求訊號", _number(row.get("demand_score"))),
        ("REPEATABILITY", "可重複銷售", _number(row.get("repeatability_score"))),
        ("AUTOMATION", "自動化潛力", _number(row.get("automation_score"))),
        ("OWNERSHIP", "資產所有權", _number(row.get("ownership_score"))),
        ("MARGIN", "低邊際成本", _number(row.get("marginal_cost_score"))),
        ("SIGNAL", "市場訊號強度", _number(row.get("signal_strength_score"))),
    ]
    ranked = sorted(dimensions, key=lambda item: item[2], reverse=True)
    return [
        {"code": code, "label": label, "score": score}
        for code, label, score in ranked
        if score >= 70
    ][:4]


def evidence_gate_checks(row: dict) -> list[dict]:
    checks = [
        (
            "INDEPENDENT_SOURCES",
            "獨立來源",
            int(row.get("independent_source_count") or 0),
            EVIDENCE_GATE_THRESHOLDS["independent_sources"],
        ),
        (
            "EVIDENCE_QUALITY",
            "Evidence Quality",
            _number(row.get("evidence_quality_score")),
            EVIDENCE_GATE_THRESHOLDS["evidence_quality"],
        ),
        (
            "SOURCE_DIVERSITY",
            "Source Diversity",
            _number(row.get("source_diversity_score")),
            EVIDENCE_GATE_THRESHOLDS["source_diversity"],
        ),
        (
            "SIGNAL_STRENGTH",
            "Signal Strength",
            _number(row.get("signal_strength_score")),
            EVIDENCE_GATE_THRESHOLDS["signal_strength"],
        ),
        (
            "CANDIDATE_SCORE",
            "Opportunity Score",
            _number(row.get("score")),
            EVIDENCE_GATE_THRESHOLDS["candidate_score"],
        ),
    ]
    return [
        {
            "code": code,
            "label": label,
            "actual": actual,
            "required": required,
            "passed": actual >= required,
        }
        for code, label, actual, required in checks
    ]


def _next_action(row: dict, report: dict | None = None, validation: dict | None = None) -> dict:
    checks = evidence_gate_checks(row)
    failed_evidence = [
        item for item in checks
        if item["code"] != "CANDIDATE_SCORE" and not item["passed"]
    ]
    score_check = next(item for item in checks if item["code"] == "CANDIDATE_SCORE")

    if failed_evidence:
        labels = "、".join(item["label"] for item in failed_evidence)
        return {
            "code": "EXPAND_EVIDENCE",
            "label": "補充多來源 Evidence",
            "reason": f"Evidence Gate 尚未通過：{labels} 未達門檻。",
            "target": "SBW-3",
        }

    if not score_check["passed"]:
        return {
            "code": "WATCH_SCORE",
            "label": "繼續觀察 Opportunity Score",
            "reason": (
                f"Evidence Gate 已具備條件，但 Score {score_check['actual']} "
                f"仍低於 CANDIDATE 門檻 {score_check['required']}。"
            ),
            "target": "OPPORTUNITY",
        }

    if row.get("status") == "CANDIDATE" and not report:
        return {
            "code": "GENERATE_RESEARCH_REPORT",
            "label": "生成 Research Report",
            "reason": "Evidence Gate 已通過，下一步建立 Buyer / Alternatives / Monetization 研究檔案。",
            "target": "SBW-3",
        }

    if row.get("status") == "CANDIDATE" and report and not validation:
        return {
            "code": "RUN_RESEARCH_VALIDATION",
            "label": "執行 Research Validation",
            "reason": "Research Report 已存在，下一步驗證 Buyer、Competitors、Pricing、WTP 與 Market Gap。",
            "target": "SBW-3",
        }

    if row.get("build_readiness") == "BUILD_READY":
        if row.get("build_proposal_status") == "PENDING_APPROVAL":
            return {
                "code": "REVIEW_BUILD_PROPOSAL",
                "label": "人工審核 Build Proposal",
                "reason": "商業驗證已通過，Build Proposal 正等待人工 APPROVE / REJECT。",
                "target": "SBW-4",
            }
        return {
            "code": "BUILD_READY",
            "label": "進入 Build Workspace",
            "reason": "Opportunity 已滿足 BUILD_READY 條件。",
            "target": "SBW-4",
        }

    return {
        "code": "REVIEW_VALIDATION",
        "label": "檢查商業驗證",
        "reason": "Opportunity 已進入候選研究流程，請檢查目前 Research / Validation 狀態。",
        "target": "SBW-3",
    }


def _list_item(row: dict) -> dict:
    next_action = _next_action(row)
    failed = [item for item in evidence_gate_checks(row) if not item["passed"]]
    return {
        "id": str(row["id"]),
        "title": row["title"] or row["canonical_title"] or "未命名機會",
        "canonical_title": row["canonical_title"],
        "asset_type": row["asset_type"],
        "score": _number(row["score"]),
        "stage": row["status"],
        "independent_source_count": int(row["independent_source_count"] or 0),
        "evidence_count": int(row["evidence_count"] or 0),
        "evidence_quality_score": _number(row["evidence_quality_score"]),
        "source_diversity_score": _number(row["source_diversity_score"]),
        "signal_strength_score": _number(row["signal_strength_score"]),
        "evidence_gate_passed": bool(row["evidence_gate_passed"]),
        "research_validation_score": _number(row["research_validation_score"]),
        "build_readiness": row["build_readiness"],
        "build_proposal_status": row["build_proposal_status"],
        "gate_failed_checks": [item["code"] for item in failed],
        "next_action": next_action,
        "updated_at": row["updated_at"],
    }


def list_opportunity_workspace(
    *,
    q: str | None = None,
    stage: str | None = None,
    asset_type: str | None = None,
    gate: str | None = None,
    build_readiness: str | None = None,
    min_score: float | None = None,
    min_sources: int | None = None,
    sort: str = "priority",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    normalized_stage = (stage or "").strip().upper() or None
    normalized_asset = (asset_type or "").strip().upper() or None
    normalized_gate = (gate or "").strip().upper() or None
    normalized_build = (build_readiness or "").strip().upper() or None
    normalized_sort = (sort or "priority").strip().lower()
    normalized_order = (order or "desc").strip().lower()

    if normalized_stage and normalized_stage not in ALLOWED_STAGES:
        raise ValueError("Unsupported opportunity stage")
    if normalized_asset and normalized_asset not in ALLOWED_ASSET_TYPES:
        raise ValueError("Unsupported asset type")
    if normalized_gate and normalized_gate not in ALLOWED_GATE:
        raise ValueError("gate must be PASS or BLOCKED")
    if normalized_build and normalized_build not in ALLOWED_BUILD_READINESS:
        raise ValueError("Unsupported build readiness")
    if normalized_sort not in SORT_SQL:
        raise ValueError("Unsupported sort")
    if normalized_order not in {"asc", "desc"}:
        raise ValueError("order must be asc or desc")

    safe_limit = min(max(int(limit), 1), 200)
    safe_offset = max(int(offset), 0)
    params: dict = {"limit": safe_limit, "offset": safe_offset}
    where = [REAL_OPPORTUNITY_SQL]

    clean_q = (q or "").strip()
    if clean_q:
        where.append(
            "(o.title ILIKE :query OR COALESCE(o.canonical_title,'') ILIKE :query "
            "OR COALESCE(o.problem,'') ILIKE :query OR COALESCE(o.target_customer,'') ILIKE :query)"
        )
        params["query"] = f"%{clean_q[:120]}%"
    if normalized_stage:
        where.append("o.status=:stage")
        params["stage"] = normalized_stage
    if normalized_asset:
        where.append("o.asset_type=:asset_type")
        params["asset_type"] = normalized_asset
    if normalized_gate:
        where.append("o.evidence_gate_passed=:gate")
        params["gate"] = normalized_gate == "PASS"
    if normalized_build:
        where.append("o.build_readiness=:build_readiness")
        params["build_readiness"] = normalized_build
    if min_score is not None:
        where.append("o.score>=:min_score")
        params["min_score"] = max(0.0, min(float(min_score), 100.0))
    if min_sources is not None:
        where.append("o.independent_source_count>=:min_sources")
        params["min_sources"] = max(int(min_sources), 0)

    where_sql = " AND ".join(where)
    if normalized_sort == "priority":
        order_sql = SORT_SQL["priority"]
    else:
        order_sql = f"{SORT_SQL[normalized_sort]} {normalized_order.upper()} NULLS LAST, o.updated_at DESC"

    def _load() -> dict:
        with engine.connect() as conn:
            total = conn.execute(
                text(f"SELECT count(*) FROM digital_asset_opportunities o WHERE {where_sql}"),
                params,
            ).scalar_one()

            rows = conn.execute(
                text(
                    f"""
                    SELECT
                      o.id,o.title,o.canonical_title,o.asset_type,o.score,o.status,
                      o.independent_source_count,o.evidence_count,o.evidence_quality_score,
                      o.source_diversity_score,o.signal_strength_score,o.evidence_gate_passed,
                      o.research_validation_score,o.build_readiness,o.build_proposal_status,
                      o.updated_at
                    FROM digital_asset_opportunities o
                    WHERE {where_sql}
                    ORDER BY {order_sql}
                    LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            ).mappings().all()

            stage_rows = conn.execute(
                text(
                    f"""
                    SELECT o.status,count(*) AS count
                    FROM digital_asset_opportunities o
                    WHERE {REAL_OPPORTUNITY_SQL}
                    GROUP BY o.status
                    ORDER BY o.status
                    """
                )
            ).mappings().all()

            type_rows = conn.execute(
                text(
                    f"""
                    SELECT o.asset_type,count(*) AS count
                    FROM digital_asset_opportunities o
                    WHERE {REAL_OPPORTUNITY_SQL}
                    GROUP BY o.asset_type
                    ORDER BY count DESC,o.asset_type
                    """
                )
            ).mappings().all()

            gate_row = conn.execute(
                text(
                    f"""
                    SELECT
                      count(*) FILTER (WHERE o.evidence_gate_passed) AS pass,
                      count(*) FILTER (WHERE NOT o.evidence_gate_passed) AS blocked
                    FROM digital_asset_opportunities o
                    WHERE {REAL_OPPORTUNITY_SQL}
                    """
                )
            ).mappings().one()

        return {
            "total": int(total or 0),
            "rows": [dict(row) for row in rows],
            "stages": {row["status"]: int(row["count"]) for row in stage_rows},
            "asset_types": {row["asset_type"]: int(row["count"]) for row in type_rows},
            "gate": {
                "PASS": int(gate_row["pass"] or 0),
                "BLOCKED": int(gate_row["blocked"] or 0),
            },
        }

    data = read_with_retry("opportunity_workspace_list", _load)
    return {
        "status": "ok",
        "filters": {
            "q": clean_q or None,
            "stage": normalized_stage,
            "asset_type": normalized_asset,
            "gate": normalized_gate,
            "build_readiness": normalized_build,
            "min_score": params.get("min_score"),
            "min_sources": params.get("min_sources"),
            "sort": normalized_sort,
            "order": normalized_order,
        },
        "pagination": {
            "total": data["total"],
            "limit": safe_limit,
            "offset": safe_offset,
            "has_more": safe_offset + safe_limit < data["total"],
        },
        "facets": {
            "stages": data["stages"],
            "asset_types": data["asset_types"],
            "gate": data["gate"],
        },
        "items": [_list_item(row) for row in data["rows"]],
    }


def _lifecycle(row: dict, report: dict | None, validation: dict | None, build_proposal: dict | None) -> list[dict]:
    gate = bool(row.get("evidence_gate_passed"))
    is_candidate = row.get("status") == "CANDIDATE"
    validation_current = bool(validation and validation.get("validation_status") == "CURRENT")
    build_ready = row.get("build_readiness") == "BUILD_READY"

    return [
        {"stage": "DISCOVERED", "status": "DONE"},
        {
            "stage": "EVIDENCE",
            "status": "DONE" if gate else "CURRENT",
        },
        {
            "stage": "CANDIDATE",
            "status": "DONE" if is_candidate else ("BLOCKED" if not gate else "CURRENT"),
        },
        {
            "stage": "RESEARCH",
            "status": "DONE" if report else ("CURRENT" if is_candidate else "PENDING"),
        },
        {
            "stage": "VALIDATION",
            "status": "DONE" if validation_current else ("CURRENT" if report else "PENDING"),
        },
        {
            "stage": "BUILD_READY",
            "status": "DONE" if build_ready else "PENDING",
        },
        {
            "stage": "BUILD_PROPOSAL",
            "status": (
                build_proposal.get("proposal_status")
                if build_proposal
                else "PENDING"
            ),
        },
    ]


def get_opportunity_workspace(opportunity_id: UUID) -> dict:
    def _load() -> dict:
        with engine.connect() as conn:
            opportunity = conn.execute(
                text(
                    f"""
                    SELECT
                      o.id,o.title,o.canonical_title,o.asset_type,o.problem,o.target_customer,
                      o.monetization_model,o.source_url,o.score,o.demand_score,
                      o.repeatability_score,o.automation_score,o.ownership_score,
                      o.marginal_cost_score,o.evidence_score,o.repeatable_sale,
                      o.update_automation,o.status,o.independent_source_count,o.evidence_count,
                      o.evidence_quality_score,o.source_diversity_score,o.signal_strength_score,
                      o.evidence_gate_passed,o.cluster_confidence,o.research_validation_score,
                      o.build_readiness,o.build_proposal_status,o.created_at,o.updated_at
                    FROM digital_asset_opportunities o
                    WHERE o.id=:id AND {REAL_OPPORTUNITY_SQL}
                    """
                ),
                {"id": opportunity_id},
            ).mappings().one_or_none()
            if opportunity is None:
                return {}

            evidence = conn.execute(
                text(
                    """
                    SELECT
                      e.id,e.signal_type,e.excerpt,e.source_url,e.source_domain,
                      e.source_class,e.source_quality,e.signal_strength,e.confidence,
                      e.discovered_at,d.title AS document_title
                    FROM opportunity_evidence oe
                    JOIN evidence e ON e.id=oe.evidence_id
                    LEFT JOIN documents d ON d.id=e.document_id
                    WHERE oe.opportunity_id=:id
                    ORDER BY e.source_quality DESC,e.signal_strength DESC,e.discovered_at DESC
                    """
                ),
                {"id": opportunity_id},
            ).mappings().all()

            aliases = conn.execute(
                text(
                    """
                    SELECT alias,source_url,created_at
                    FROM opportunity_aliases
                    WHERE opportunity_id=:id
                    ORDER BY created_at DESC,id DESC
                    LIMIT 50
                    """
                ),
                {"id": opportunity_id},
            ).mappings().all()

            report = conn.execute(
                text(
                    """
                    SELECT
                      id,report_status,problem,buyer,existing_alternatives,evidence,
                      monetization,build_complexity,risks,why_now,observe_only,
                      generator_version,generated_at,updated_at
                    FROM research_reports
                    WHERE opportunity_id=:id
                    """
                ),
                {"id": opportunity_id},
            ).mappings().one_or_none()

            validation = conn.execute(
                text(
                    """
                    SELECT
                      id,validation_status,buyer_status,competitors_status,pricing_status,
                      willingness_to_pay_status,market_gap_status,completeness_score,
                      validation_gate_passed,build_readiness,observe_only,validator_version,
                      validated_at,updated_at
                    FROM research_validations
                    WHERE opportunity_id=:id
                    """
                ),
                {"id": opportunity_id},
            ).mappings().one_or_none()

            build_proposal = conn.execute(
                text(
                    """
                    SELECT
                      id,revision,proposal_status,title,objective,artifact_type,
                      proposed_stack,requires_human_approval,execution_enabled,
                      generated_at,updated_at,approved_at,rejected_at
                    FROM build_proposals
                    WHERE opportunity_id=:id
                    """
                ),
                {"id": opportunity_id},
            ).mappings().one_or_none()

        return {
            "opportunity": dict(opportunity),
            "evidence": [dict(row) for row in evidence],
            "aliases": [dict(row) for row in aliases],
            "report": dict(report) if report else None,
            "validation": dict(validation) if validation else None,
            "build_proposal": dict(build_proposal) if build_proposal else None,
        }

    data = read_with_retry("opportunity_workspace_detail", _load)
    if not data:
        raise LookupError("Opportunity not found")

    opportunity = data["opportunity"]
    report = data["report"]
    validation = data["validation"]
    build_proposal = data["build_proposal"]

    source_classes = Counter(
        (row.get("source_class") or "unknown")
        for row in data["evidence"]
    )
    domains = sorted({
        (row.get("source_domain") or "").lower()
        for row in data["evidence"]
        if row.get("source_domain")
    })

    checks = evidence_gate_checks(opportunity)
    failed_checks = [item for item in checks if not item["passed"]]
    next_action = _next_action(opportunity, report, validation)

    return {
        "status": "ok",
        "opportunity": {
            "id": str(opportunity["id"]),
            "title": opportunity["title"],
            "canonical_title": opportunity["canonical_title"],
            "asset_type": opportunity["asset_type"],
            "problem": opportunity["problem"],
            "target_customer": opportunity["target_customer"],
            "monetization_model": opportunity["monetization_model"],
            "source_url": opportunity["source_url"],
            "score": _number(opportunity["score"]),
            "stage": opportunity["status"],
            "independent_source_count": int(opportunity["independent_source_count"] or 0),
            "evidence_count": int(opportunity["evidence_count"] or 0),
            "evidence_quality_score": _number(opportunity["evidence_quality_score"]),
            "source_diversity_score": _number(opportunity["source_diversity_score"]),
            "signal_strength_score": _number(opportunity["signal_strength_score"]),
            "evidence_gate_passed": bool(opportunity["evidence_gate_passed"]),
            "research_validation_score": _number(opportunity["research_validation_score"]),
            "build_readiness": opportunity["build_readiness"],
            "build_proposal_status": opportunity["build_proposal_status"],
            "created_at": opportunity["created_at"],
            "updated_at": opportunity["updated_at"],
        },
        "scorecard": {
            "demand": _number(opportunity["demand_score"]),
            "repeatability": _number(opportunity["repeatability_score"]),
            "automation": _number(opportunity["automation_score"]),
            "ownership": _number(opportunity["ownership_score"]),
            "marginal_cost": _number(opportunity["marginal_cost_score"]),
            "evidence": _number(opportunity["evidence_score"]),
        },
        "why_worth_attention": _worth_attention(opportunity),
        "gate": {
            "passed": bool(opportunity["evidence_gate_passed"]),
            "checks": checks,
            "failed_checks": [item["code"] for item in failed_checks],
        },
        "evidence": {
            "summary": {
                "records": len(data["evidence"]),
                "independent_sources": int(opportunity["independent_source_count"] or 0),
                "domains": domains,
                "source_classes": dict(sorted(source_classes.items())),
            },
            "items": [
                {
                    "id": str(row["id"]),
                    "signal_type": row["signal_type"],
                    "excerpt": row["excerpt"],
                    "source_url": row["source_url"],
                    "source_domain": row["source_domain"],
                    "source_class": row["source_class"],
                    "source_quality": _number(row["source_quality"]),
                    "signal_strength": _number(row["signal_strength"]),
                    "confidence": _number(row["confidence"]),
                    "document_title": row["document_title"],
                    "discovered_at": row["discovered_at"],
                }
                for row in data["evidence"]
            ],
        },
        "aliases": [
            {
                "alias": row["alias"],
                "source_url": row["source_url"],
                "created_at": row["created_at"],
            }
            for row in data["aliases"]
        ],
        "research": report,
        "validation": validation,
        "build_proposal": build_proposal,
        "lifecycle": _lifecycle(opportunity, report, validation, build_proposal),
        "next_action": next_action,
    }
