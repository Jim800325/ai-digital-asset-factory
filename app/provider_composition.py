from __future__ import annotations

import hashlib
import json
from itertools import product

from sqlalchemy import text

from app.config import settings
from app.db import engine


COMPOSITION_TEMPLATES = [
    {
        "template_id": "MARKET_INTELLIGENCE_AUTOMATION",
        "opportunity_title": "Automated market intelligence monitoring API",
        "target_customer": "small businesses, operators, analysts, and niche research teams",
        "monetization_model": "subscription / API / intelligence report",
        "hypothesis": (
            "Teams need an alternative to manual, expensive, and slow market monitoring. "
            "A reusable collection plus intelligence plus automation stack can provide "
            "recurring monitored data, reports, alerts, and API access."
        ),
        "required_slots": [
            ("collection", {"DATA_COLLECTION", "MONITORING", "BROWSER_AUTOMATION"}),
            ("intelligence", {"AI_APP_PLATFORM", "AGENT_WORKFLOW"}),
            ("automation", {"AUTOMATION"}),
        ],
        "optional_slots": [
            ("distribution", {"DISTRIBUTION", "NEWSLETTER"}),
        ],
    },
    {
        "template_id": "AGENTIC_RESEARCH_FACTORY",
        "opportunity_title": "Automated research report workflow tool",
        "target_customer": "consultants, founders, analysts, and small research teams",
        "monetization_model": "subscription / report / workflow template",
        "hypothesis": (
            "Teams need a faster alternative to manual research that takes hours. "
            "A browser or data collection layer combined with an agent workflow and "
            "automation can repeatedly produce research summaries, reports, and data products."
        ),
        "required_slots": [
            ("collection", {"DATA_COLLECTION", "BROWSER_AUTOMATION"}),
            ("intelligence", {"AGENT_WORKFLOW", "AI_APP_PLATFORM"}),
            ("automation", {"AUTOMATION"}),
        ],
        "optional_slots": [
            ("distribution", {"DISTRIBUTION", "NEWSLETTER"}),
        ],
    },
    {
        "template_id": "MONITORING_ALERTS_SERVICE",
        "opportunity_title": "Automated change monitoring alert subscription tool",
        "target_customer": "e-commerce teams, operators, researchers, and compliance teams",
        "monetization_model": "subscription / alert service / monitored dataset",
        "hypothesis": (
            "Customers need an alternative to manual website and market checks. "
            "A monitoring plus automation plus distribution stack can turn difficult, "
            "repetitive tracking into recurring alerts, datasets, and subscription reports."
        ),
        "required_slots": [
            ("monitoring", {"MONITORING"}),
            ("automation", {"AUTOMATION"}),
            ("distribution", {"DISTRIBUTION", "NEWSLETTER"}),
        ],
        "optional_slots": [
            ("collection", {"DATA_COLLECTION", "BROWSER_AUTOMATION"}),
        ],
    },
]

ROLE_MONTHLY_COST_USD = {
    "AUTOMATION": 10,
    "AGENT_WORKFLOW": 12,
    "AI_APP_PLATFORM": 18,
    "DATA_COLLECTION": 8,
    "MONITORING": 8,
    "DISTRIBUTION": 8,
    "NEWSLETTER": 8,
    "BROWSER_AUTOMATION": 15,
    "OTHER": 10,
}


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def composition_key(template_id: str, members: list[dict]) -> str:
    identity = {
        "template_id": template_id,
        "members": [
            {
                "slot": item["slot"],
                "repo_full_name": item["repo_full_name"].lower(),
            }
            for item in sorted(members, key=lambda row: row["slot"])
        ],
    }
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


def license_compatibility(members: list[dict]) -> tuple[str, list[str]]:
    policies = {str(item.get("license_policy") or "UNKNOWN").upper() for item in members}
    constraints: list[str] = []
    for item in members:
        policy = str(item.get("license_policy") or "UNKNOWN").upper()
        fit = str(item.get("commercial_fit") or "REVIEW_REQUIRED")
        repo = item["repo_full_name"]
        if policy in {"UNKNOWN", "RESTRICTED"}:
            constraints.append(f"{repo}: {policy}; commercial eligibility unresolved")
        elif policy in {"COPYLEFT", "CONDITIONAL"}:
            constraints.append(f"{repo}: {policy}; {fit}")
        elif "REQUIRED" in fit or "RECOMMENDED" in fit or "SINGLE_TENANT" in fit:
            constraints.append(f"{repo}: {fit}")

    if policies & {"UNKNOWN", "RESTRICTED"}:
        return "BLOCKED", constraints
    if policies & {"COPYLEFT", "CONDITIONAL"}:
        return "REVIEW", constraints
    return "PASS", constraints


def estimate_operating_cost(members: list[dict]) -> dict:
    baseline = sum(
        ROLE_MONTHLY_COST_USD.get(str(item.get("provider_role") or "OTHER"), 10)
        for item in members
    )
    baseline = max(10, baseline)
    return {
        "currency": "USD",
        "monthly_low": baseline,
        "monthly_high": baseline * 3,
        "estimate_basis": "ROLE_HEURISTIC_V0.2",
        "includes": ["compute", "storage", "scheduler overhead"],
        "excludes": ["paid model tokens", "paid proxies", "third-party SaaS plans"],
    }


def evaluate_composition(template: dict, members: list[dict]) -> dict:
    compatibility, constraints = license_compatibility(members)
    scores = [float(item.get("score") or 0) for item in members]
    average = sum(scores) / len(scores) if scores else 0.0
    minimum = min(scores) if scores else 0.0
    license_score = {"PASS": 100.0, "REVIEW": 75.0, "BLOCKED": 0.0}[compatibility]
    stack_score = round(
        average * 0.75
        + minimum * 0.15
        + license_score * 0.10,
        2,
    )
    threshold = float(settings.side_business_composition_ready_score)
    if compatibility == "BLOCKED":
        status = "BLOCKED"
    elif stack_score >= threshold:
        status = "ACTIVE"
    else:
        status = "WATCH"

    key = composition_key(template["template_id"], members)
    provider_snapshot = [
        {
            "provider_id": str(item["id"]),
            "repo_full_name": item["repo_full_name"],
            "slot": item["slot"],
            "provider_role": item["provider_role"],
            "provider_score": round(float(item.get("score") or 0), 2),
            "license_policy": item["license_policy"],
            "commercial_fit": item["commercial_fit"],
        }
        for item in sorted(members, key=lambda row: (row["slot"], row["repo_full_name"]))
    ]
    cost = estimate_operating_cost(members)
    evidence_payload = {
        "composition_key": key,
        "template_id": template["template_id"],
        "hypothesis": template["hypothesis"],
        "target_customer": template["target_customer"],
        "monetization_model": template["monetization_model"],
        "providers": provider_snapshot,
        "license_compatibility": compatibility,
        "commercial_constraints": constraints,
        "operating_cost": cost,
    }
    evidence_sha = hashlib.sha256(
        _canonical_json(evidence_payload).encode("utf-8")
    ).hexdigest()
    return {
        "composition_key": key,
        "template_id": template["template_id"],
        "opportunity_title": template["opportunity_title"],
        "hypothesis": template["hypothesis"],
        "target_customer": template["target_customer"],
        "monetization_model": template["monetization_model"],
        "stack_score": stack_score,
        "license_compatibility": compatibility,
        "commercial_constraints": constraints,
        "operating_cost": cost,
        "provider_snapshot": provider_snapshot,
        "evidence_payload_sha256": evidence_sha,
        "composition_status": status,
        "members": members,
    }


def _slot_candidates(
    providers: list[dict],
    roles: set[str],
    *,
    limit: int,
) -> list[dict]:
    matches = [
        item
        for item in providers
        if item.get("provider_role") in roles
        and item.get("readiness") == "BUILD_READY"
        and item.get("queue_status") == "QUEUED"
        and bool(item.get("active", True))
    ]
    matches.sort(
        key=lambda item: (
            -float(item.get("score") or 0),
            -int(item.get("stars") or 0),
            item["repo_full_name"].lower(),
        )
    )
    return matches[:limit]


def plan_compositions(
    providers: list[dict],
    *,
    candidates_per_slot: int | None = None,
    max_results: int | None = None,
) -> list[dict]:
    per_slot = max(
        1,
        int(candidates_per_slot or settings.side_business_composition_candidates_per_slot),
    )
    max_plans = max(
        1,
        int(max_results or settings.side_business_composition_max_per_run),
    )
    plans: list[dict] = []

    for template in COMPOSITION_TEMPLATES:
        required: list[tuple[str, list[dict]]] = []
        missing = False
        for slot, roles in template["required_slots"]:
            candidates = _slot_candidates(providers, roles, limit=per_slot)
            if not candidates:
                missing = True
                break
            required.append((slot, candidates))
        if missing:
            continue

        for combination in product(*(rows for _, rows in required)):
            provider_ids = [str(item["id"]) for item in combination]
            if len(set(provider_ids)) != len(provider_ids):
                continue

            members = []
            used = set()
            for (slot, _), provider in zip(required, combination):
                member = dict(provider)
                member["slot"] = slot
                members.append(member)
                used.add(str(provider["id"]))

            for slot, roles in template.get("optional_slots", []):
                optional = _slot_candidates(providers, roles, limit=per_slot)
                selected = next(
                    (item for item in optional if str(item["id"]) not in used),
                    None,
                )
                if selected is not None:
                    member = dict(selected)
                    member["slot"] = slot
                    members.append(member)
                    used.add(str(selected["id"]))

            plans.append(evaluate_composition(template, members))

    unique = {}
    for plan in plans:
        current = unique.get(plan["composition_key"])
        if current is None or plan["stack_score"] > current["stack_score"]:
            unique[plan["composition_key"]] = plan
    ordered = sorted(
        unique.values(),
        key=lambda item: (
            {"ACTIVE": 0, "WATCH": 1, "BLOCKED": 2}[item["composition_status"]],
            -float(item["stack_score"]),
            item["template_id"],
            item["composition_key"],
        ),
    )
    return ordered[:max_plans]


def _load_build_ready_providers(limit: int = 250) -> list[dict]:
    with engine.connect() as db:
        rows = db.execute(
            text("""
              SELECT p.id,p.repo_full_name,p.repo_url,p.provider_role,
                     p.license_policy,p.commercial_fit,p.commercial_model,
                     p.score,p.stars,p.readiness,p.active,q.queue_status
              FROM side_business_build_queue q
              JOIN side_business_providers p ON p.id=q.provider_id
              WHERE q.queue_status='QUEUED'
                AND p.active=true
                AND p.readiness='BUILD_READY'
              ORDER BY q.provider_score DESC,p.stars DESC,p.repo_full_name
              LIMIT :limit
            """),
            {"limit": max(1, min(int(limit), 500))},
        ).mappings().all()
    return [dict(row) for row in rows]


def run_provider_composition_cycle() -> dict:
    with engine.begin() as db:
        run_id = db.execute(
            text(
                "INSERT INTO side_business_composition_runs DEFAULT VALUES RETURNING id"
            )
        ).scalar_one()

    providers = _load_build_ready_providers()
    errors: list[str] = []
    plans: list[dict] = []
    try:
        plans = plan_compositions(providers)
    except Exception as exc:
        errors.append(str(exc))

    counters = {
        "providers_considered": len(providers),
        "compositions_generated": 0,
        "active_compositions": 0,
        "watch_compositions": 0,
        "blocked_compositions": 0,
        "stale_compositions": 0,
        "hypotheses_emitted": 0,
    }

    if not errors:
        with engine.begin() as db:
            for plan in plans:
                composition_id = db.execute(
                    text("""
                      INSERT INTO side_business_compositions(
                        composition_key,template_id,opportunity_title,hypothesis,
                        target_customer,monetization_model,stack_score,
                        license_compatibility,commercial_constraints,operating_cost,
                        provider_snapshot,evidence_payload_sha256,composition_status,
                        last_generated_run_id,last_generated_at,updated_at)
                      VALUES(
                        :composition_key,:template_id,:opportunity_title,:hypothesis,
                        :target_customer,:monetization_model,:stack_score,
                        :license_compatibility,CAST(:commercial_constraints AS jsonb),
                        CAST(:operating_cost AS jsonb),CAST(:provider_snapshot AS jsonb),
                        :evidence_payload_sha256,:composition_status,:run_id,now(),now())
                      ON CONFLICT(composition_key) DO UPDATE SET
                        template_id=excluded.template_id,
                        opportunity_title=excluded.opportunity_title,
                        hypothesis=excluded.hypothesis,
                        target_customer=excluded.target_customer,
                        monetization_model=excluded.monetization_model,
                        stack_score=excluded.stack_score,
                        license_compatibility=excluded.license_compatibility,
                        commercial_constraints=excluded.commercial_constraints,
                        operating_cost=excluded.operating_cost,
                        provider_snapshot=excluded.provider_snapshot,
                        evidence_payload_sha256=excluded.evidence_payload_sha256,
                        composition_status=excluded.composition_status,
                        last_generated_run_id=excluded.last_generated_run_id,
                        last_generated_at=now(),
                        updated_at=now()
                      RETURNING id
                    """),
                    {
                        **{
                            key: value
                            for key, value in plan.items()
                            if key not in {
                                "members",
                                "commercial_constraints",
                                "operating_cost",
                                "provider_snapshot",
                            }
                        },
                        "commercial_constraints": _canonical_json(plan["commercial_constraints"]),
                        "operating_cost": _canonical_json(plan["operating_cost"]),
                        "provider_snapshot": _canonical_json(plan["provider_snapshot"]),
                        "run_id": run_id,
                    },
                ).scalar_one()

                db.execute(
                    text(
                        "DELETE FROM side_business_composition_members "
                        "WHERE composition_id=:id"
                    ),
                    {"id": composition_id},
                )
                for position, member in enumerate(
                    sorted(plan["members"], key=lambda row: row["slot"])
                ):
                    db.execute(
                        text("""
                          INSERT INTO side_business_composition_members(
                            composition_id,provider_id,slot,provider_role,
                            provider_score,license_policy,commercial_fit,position)
                          VALUES(
                            :composition_id,:provider_id,:slot,:provider_role,
                            :provider_score,:license_policy,:commercial_fit,:position)
                        """),
                        {
                            "composition_id": composition_id,
                            "provider_id": member["id"],
                            "slot": member["slot"],
                            "provider_role": member["provider_role"],
                            "provider_score": float(member.get("score") or 0),
                            "license_policy": member["license_policy"],
                            "commercial_fit": member["commercial_fit"],
                            "position": position,
                        },
                    )

                counters["compositions_generated"] += 1
                if plan["composition_status"] == "ACTIVE":
                    counters["active_compositions"] += 1
                    counters["hypotheses_emitted"] += 1
                elif plan["composition_status"] == "WATCH":
                    counters["watch_compositions"] += 1
                else:
                    counters["blocked_compositions"] += 1

            counters["stale_compositions"] = int(
                db.execute(
                    text("""
                      WITH changed AS (
                        UPDATE side_business_compositions
                        SET composition_status='STALE',updated_at=now()
                        WHERE composition_status<>'STALE'
                          AND (
                            last_generated_run_id IS NULL
                            OR last_generated_run_id<>:run_id
                          )
                        RETURNING id
                      )
                      SELECT COUNT(*) FROM changed
                    """),
                    {"run_id": run_id},
                ).scalar_one()
            )

    if errors:
        status = "FAILED"
    elif not providers:
        status = "SKIPPED"
    else:
        status = "SUCCESS"

    with engine.begin() as db:
        db.execute(
            text("""
              UPDATE side_business_composition_runs
              SET status=:status,
                  providers_considered=:providers_considered,
                  compositions_generated=:compositions_generated,
                  active_compositions=:active_compositions,
                  watch_compositions=:watch_compositions,
                  blocked_compositions=:blocked_compositions,
                  stale_compositions=:stale_compositions,
                  hypotheses_emitted=:hypotheses_emitted,
                  errors=:errors,
                  error_summary=:error_summary,
                  finished_at=now()
              WHERE id=:id
            """),
            {
                "id": run_id,
                "status": status,
                **counters,
                "errors": len(errors),
                "error_summary": "\n".join(errors[:20])[:8000] if errors else None,
            },
        )

    return {
        "run_id": str(run_id),
        "status": status,
        **counters,
        "errors": len(errors),
    }


def list_provider_compositions(
    limit: int = 100,
    status: str | None = None,
) -> list[dict]:
    normalized = status.upper().strip() if status else None
    allowed = {"ACTIVE", "WATCH", "BLOCKED", "STALE"}
    if normalized and normalized not in allowed:
        raise ValueError("status must be ACTIVE, WATCH, BLOCKED, or STALE")

    sql = """
      SELECT id,composition_key,template_id,opportunity_title,hypothesis,
             target_customer,monetization_model,stack_score,
             license_compatibility,commercial_constraints,operating_cost,
             provider_snapshot,evidence_payload_sha256,composition_status,
             last_generated_run_id,last_generated_at,created_at,updated_at
      FROM side_business_compositions
      WHERE 1=1
    """
    params = {"limit": max(1, min(int(limit), 500))}
    if normalized:
        sql += " AND composition_status=:status"
        params["status"] = normalized
    sql += " ORDER BY stack_score DESC,updated_at DESC LIMIT :limit"

    with engine.connect() as db:
        return [dict(row) for row in db.execute(text(sql), params).mappings().all()]


def list_provider_composition_runs(limit: int = 30) -> list[dict]:
    with engine.connect() as db:
        rows = db.execute(
            text("""
              SELECT id,status,providers_considered,compositions_generated,
                     active_compositions,watch_compositions,blocked_compositions,
                     stale_compositions,hypotheses_emitted,errors,error_summary,
                     started_at,finished_at
              FROM side_business_composition_runs
              ORDER BY started_at DESC
              LIMIT :limit
            """),
            {"limit": max(1, min(int(limit), 200))},
        ).mappings().all()
    return [dict(row) for row in rows]


def composition_discovery_items(limit: int | None = None) -> list[dict]:
    emit_limit = max(
        1,
        min(
            int(limit or settings.side_business_composition_emit_limit),
            100,
        ),
    )
    with engine.connect() as db:
        rows = db.execute(
            text("""
              SELECT composition_key,template_id,opportunity_title,hypothesis,
                     target_customer,monetization_model,stack_score,
                     license_compatibility,commercial_constraints,operating_cost,
                     provider_snapshot,evidence_payload_sha256
              FROM side_business_compositions
              WHERE composition_status='ACTIVE'
              ORDER BY stack_score DESC,updated_at DESC
              LIMIT :limit
            """),
            {"limit": emit_limit},
        ).mappings().all()

    items = []
    for row in rows:
        providers = row["provider_snapshot"]
        if isinstance(providers, str):
            providers = json.loads(providers)
        provider_text = "; ".join(
            f"{item['slot']}={item['repo_full_name']} "
            f"({item['provider_role']}, score {float(item['provider_score']):.2f})"
            for item in providers
        )
        constraints = row["commercial_constraints"]
        if isinstance(constraints, str):
            constraints = json.loads(constraints)
        cost = row["operating_cost"]
        if isinstance(cost, str):
            cost = json.loads(cost)

        material = (
            f"{row['hypothesis']} "
            f"Target customer: {row['target_customer']}. "
            f"Monetization: {row['monetization_model']}. "
            f"Technical stack: {provider_text}. "
            f"Stack score: {float(row['stack_score']):.2f}. "
            f"License compatibility: {row['license_compatibility']}. "
            f"Commercial constraints: {constraints or ['none detected']}. "
            f"Estimated self-hosted operating cost: "
            f"USD {cost.get('monthly_low')}-{cost.get('monthly_high')} per month. "
            "This is technical/commercial composition evidence only. "
            "The opportunity still needs independent market evidence, buyer validation, "
            "competitor and pricing validation, willingness-to-pay evidence, and the "
            "normal Product BUILD_READY gate before any build proposal."
        )
        key = str(row["composition_key"])
        items.append(
            {
                "source_type": "GITHUB_SIDE_BUSINESS_COMPOSITION",
                "url": (
                    "https://github.com/Jim800325/ai-digital-asset-factory"
                    f"?composition={key[:16]}"
                ),
                "title": row["opportunity_title"],
                "text": material[:30000],
                "external_id": key,
                "fingerprint": hashlib.sha256(
                    ("side-business-composition:" + key).encode("utf-8")
                ).hexdigest(),
            }
        )
    return items
