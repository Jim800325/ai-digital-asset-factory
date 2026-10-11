from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy import text

from app.db import engine
from app.db_reliability import read_with_retry
from app.opportunity_workspace import EVIDENCE_GATE_THRESHOLDS

PLAN_VERSION = "sbw3-evidence-search-plan-v1"

SOURCE_STRATEGIES = (
    {
        "source_class": "community",
        "label": "社群需求訊號",
        "expected_quality": 82.0,
        "preferred_domains": ["reddit.com", "news.ycombinator.com", "stackoverflow.com"],
        "intents": [
            "{seed} pain problem workflow",
            "{seed} alternative recommendation",
            "{seed} manual expensive frustrating",
        ],
        "evidence_goal": "找到真實使用者問題、抱怨、替代方案需求或工作流痛點。",
    },
    {
        "source_class": "product",
        "label": "競品 / 市場供給",
        "expected_quality": 72.0,
        "preferred_domains": ["producthunt.com", "g2.com", "capterra.com"],
        "intents": [
            "{seed} software alternative",
            "{seed} product pricing",
            "{seed} reviews competitors",
        ],
        "evidence_goal": "確認已有產品、競品、評論或付費市場存在，並觀察差異化空間。",
    },
    {
        "source_class": "web",
        "label": "Pricing / Buyer / 商業頁面",
        "expected_quality": 58.0,
        "preferred_domains": [],
        "intents": [
            "{seed} pricing",
            "{seed} API pricing",
            "{seed} buy subscription service",
        ],
        "evidence_goal": "找到定價、Buyer、方案頁、公開商業條款或付費 CTA。",
    },
    {
        "source_class": "code",
        "label": "開源 / 技術需求",
        "expected_quality": 78.0,
        "preferred_domains": ["github.com", "gitlab.com"],
        "intents": [
            "{seed} github issue",
            "{seed} feature request",
            "{seed} open source tool",
        ],
        "evidence_goal": "找到不同專案中的技術需求、Issue、Feature Request 或實作替代品。",
    },
    {
        "source_class": "news",
        "label": "時效 / Why Now",
        "expected_quality": 68.0,
        "preferred_domains": [],
        "intents": [
            "{seed} launch market trend",
            "{seed} industry adoption",
        ],
        "evidence_goal": "補充近期市場變化、採用趨勢或 Why Now 訊號。",
    },
)

ASSET_INTENTS = {
    "DATASET_API": [
        "{seed} API",
        "{seed} data provider",
        "{seed} dataset pricing",
    ],
    "INTELLIGENCE_REPORT": [
        "{seed} market report",
        "{seed} intelligence service",
        "{seed} research subscription",
    ],
    "MICRO_SAAS_TOOL": [
        "{seed} SaaS alternative",
        "{seed} workflow tool",
        "{seed} pricing software",
    ],
    "TEMPLATE_WORKFLOW": [
        "{seed} template workflow",
        "{seed} automation template",
        "{seed} manual process",
    ],
    "CONTENT_IP": [
        "{seed} creator demand",
        "{seed} content subscription",
        "{seed} audience problem",
    ],
}

STOPWORDS = {
    "the","and","for","with","from","this","that","into","next","stored","today",
    "github","issue","issues","request","requests","feature","new","links","repositories",
}


def _number(value) -> float:
    return round(float(value or 0), 2)


def _search_seed(row: dict) -> str:
    raw = row.get("canonical_title") or row.get("title") or ""
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9+.#_-]*", raw.lower())
    meaningful = [
        token for token in tokens
        if len(token) >= 3 and token not in STOPWORDS and not token.isdigit()
    ]
    if not meaningful:
        fallback = re.findall(r"[A-Za-z0-9][A-Za-z0-9+.#_-]*", row.get("title") or "")
        meaningful = [token.lower() for token in fallback if len(token) >= 3]
    return " ".join(dict.fromkeys(meaningful[:8]))[:120] or "digital asset opportunity"


def _gap(actual: float, required: float) -> float:
    return round(max(0.0, float(required) - float(actual)), 2)


def _requirements(row: dict) -> list[dict]:
    values = (
        ("INDEPENDENT_SOURCES", "獨立來源", int(row.get("independent_source_count") or 0), EVIDENCE_GATE_THRESHOLDS["independent_sources"]),
        ("EVIDENCE_QUALITY", "Evidence Quality", _number(row.get("evidence_quality_score")), EVIDENCE_GATE_THRESHOLDS["evidence_quality"]),
        ("SOURCE_DIVERSITY", "Source Diversity", _number(row.get("source_diversity_score")), EVIDENCE_GATE_THRESHOLDS["source_diversity"]),
        ("SIGNAL_STRENGTH", "Signal Strength", _number(row.get("signal_strength_score")), EVIDENCE_GATE_THRESHOLDS["signal_strength"]),
        ("CANDIDATE_SCORE", "Opportunity Score", _number(row.get("score")), EVIDENCE_GATE_THRESHOLDS["candidate_score"]),
    )
    return [
        {
            "code": code,
            "label": label,
            "actual": actual,
            "required": required,
            "gap": _gap(actual, required),
            "passed": actual >= required,
        }
        for code, label, actual, required in values
    ]


def _task_priority(strategy: dict, existing_classes: set[str]) -> int:
    source_class = strategy["source_class"]
    if source_class not in existing_classes:
        if source_class == "community":
            return 10
        if source_class == "product":
            return 20
        if source_class == "web":
            return 30
        if source_class == "code":
            return 40
        return 50
    return 90


def _queries(seed: str, asset_type: str, strategy: dict) -> list[str]:
    templates = list(strategy["intents"])
    templates.extend(ASSET_INTENTS.get(asset_type, []))
    rendered=[]
    for template in templates:
        query=re.sub(r"\s+"," ",template.format(seed=seed)).strip()
        if query and query not in rendered:
            rendered.append(query[:180])
    return rendered[:5]


def _build_tasks(row: dict, existing_classes: set[str], existing_domains: set[str]) -> list[dict]:
    seed=_search_seed(row)
    tasks=[]
    for strategy in SOURCE_STRATEGIES:
        domains=[
            domain for domain in strategy["preferred_domains"]
            if domain not in existing_domains
        ]
        already_covered=strategy["source_class"] in existing_classes
        tasks.append({
            "task_id": f"{strategy['source_class'].upper()}_EVIDENCE",
            "priority": _task_priority(strategy, existing_classes),
            "source_class": strategy["source_class"],
            "label": strategy["label"],
            "already_covered": already_covered,
            "preferred_domains": domains,
            "exclude_domains": sorted(existing_domains),
            "queries": _queries(seed, row["asset_type"], strategy),
            "evidence_goal": strategy["evidence_goal"],
            "acceptance": {
                "must_be_new_domain": True,
                "prefer_new_source_class": not already_covered,
                "minimum_expected_source_quality": strategy["expected_quality"],
                "must_map_to_same_opportunity": True,
            },
        })

    return sorted(
        tasks,
        key=lambda task: (task["priority"], -task["acceptance"]["minimum_expected_source_quality"]),
    )[:4]


def build_evidence_search_plan(opportunity_id: UUID) -> dict:
    def _load() -> dict:
        with engine.connect() as conn:
            row=conn.execute(text("""
              SELECT
                o.id,o.title,o.canonical_title,o.asset_type,o.problem,o.target_customer,
                o.score,o.status,o.independent_source_count,o.evidence_count,
                o.evidence_quality_score,o.source_diversity_score,o.signal_strength_score,
                o.evidence_gate_passed,o.build_readiness,o.updated_at
              FROM digital_asset_opportunities o
              WHERE o.id=:id
                AND COALESCE(o.title,'') NOT LIKE '[TEST_ONLY]%'
            """),{"id":opportunity_id}).mappings().one_or_none()
            if row is None:
                return {}

            evidence=conn.execute(text("""
              SELECT DISTINCT
                lower(COALESCE(e.source_domain,'')) AS source_domain,
                lower(COALESCE(e.source_class,'unknown')) AS source_class,
                e.source_url
              FROM opportunity_evidence oe
              JOIN evidence e ON e.id=oe.evidence_id
              WHERE oe.opportunity_id=:id
              ORDER BY source_class,source_domain,e.source_url
            """),{"id":opportunity_id}).mappings().all()

        return {
            "opportunity":dict(row),
            "evidence":[dict(item) for item in evidence],
        }

    data=read_with_retry("evidence_search_plan",_load)
    if not data:
        raise LookupError("Opportunity not found")

    row=data["opportunity"]
    existing_domains={
        item["source_domain"]
        for item in data["evidence"]
        if item["source_domain"]
    }
    existing_classes={
        item["source_class"]
        for item in data["evidence"]
        if item["source_class"] and item["source_class"]!="unknown"
    }
    requirements=_requirements(row)
    failed=[item for item in requirements if not item["passed"]]
    expansion_required=not bool(row["evidence_gate_passed"]) and bool(failed)

    eligibility_reasons=[]
    if row["status"] not in {"RESEARCH","WATCH"}:
        eligibility_reasons.append("STATUS_NOT_RESEARCHABLE")
    if row["evidence_gate_passed"]:
        eligibility_reasons.append("EVIDENCE_GATE_ALREADY_PASSED")
    if _number(row["score"]) < EVIDENCE_GATE_THRESHOLDS["candidate_score"]:
        eligibility_reasons.append("SCORE_BELOW_CANDIDATE_THRESHOLD")

    executable_candidate=(
        row["status"]=="RESEARCH"
        and not row["evidence_gate_passed"]
        and _number(row["score"]) >= EVIDENCE_GATE_THRESHOLDS["candidate_score"]
    )

    tasks=_build_tasks(row,existing_classes,existing_domains) if expansion_required else []

    return {
        "status":"ok",
        "contract":"EVIDENCE_SEARCH_PLAN",
        "version":PLAN_VERSION,
        "mode":"PLAN_ONLY",
        "execution_enabled":False,
        "opportunity":{
            "id":str(row["id"]),
            "title":row["title"],
            "asset_type":row["asset_type"],
            "stage":row["status"],
            "score":_number(row["score"]),
            "build_readiness":row["build_readiness"],
            "updated_at":row["updated_at"],
        },
        "gate":{
            "passed":bool(row["evidence_gate_passed"]),
            "requirements":requirements,
            "failed_checks":[item["code"] for item in failed],
        },
        "current_evidence":{
            "records":int(row["evidence_count"] or 0),
            "independent_sources":int(row["independent_source_count"] or 0),
            "source_domains":sorted(existing_domains),
            "source_classes":sorted(existing_classes),
        },
        "search_seed":_search_seed(row),
        "objective":{
            "expansion_required":expansion_required,
            "minimum_new_independent_sources":max(
                0,
                int(EVIDENCE_GATE_THRESHOLDS["independent_sources"])
                - int(row["independent_source_count"] or 0),
            ),
            "prefer_new_source_class":(
                _number(row["source_diversity_score"])
                < EVIDENCE_GATE_THRESHOLDS["source_diversity"]
            ),
            "target_status":"CANDIDATE",
        },
        "eligibility":{
            "recommended_for_expansion":executable_candidate,
            "reasons":eligibility_reasons,
        },
        "tasks":tasks,
        "completion_criteria":{
            "independent_source_count":EVIDENCE_GATE_THRESHOLDS["independent_sources"],
            "evidence_quality_score":EVIDENCE_GATE_THRESHOLDS["evidence_quality"],
            "source_diversity_score":EVIDENCE_GATE_THRESHOLDS["source_diversity"],
            "signal_strength_score":EVIDENCE_GATE_THRESHOLDS["signal_strength"],
            "candidate_score":EVIDENCE_GATE_THRESHOLDS["candidate_score"],
            "requires_reaggregation":True,
            "requires_same_opportunity_mapping":True,
        },
        "safety":{
            "network_execution":False,
            "database_write":False,
            "automatic_ingest":False,
            "automatic_candidate_promotion":False,
            "production_provider_writes":False,
        },
    }
