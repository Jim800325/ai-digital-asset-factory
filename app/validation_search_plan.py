from __future__ import annotations

from uuid import UUID

from sqlalchemy import text

from app.db import engine
from app.db_reliability import read_with_retry
from app.evidence_search_plan import _search_seed

PLAN_VERSION="sbw3-validation-search-plan-v1"

TARGET_STATUS={
    "buyer":"PARTIAL",
    "competitors":"PARTIAL",
    "pricing":"PARTIAL",
    "willingness_to_pay":"VALIDATED",
    "market_gap":"VALIDATED",
}
STATUS_RANK={"UNKNOWN":0,"PARTIAL":1,"VALIDATED":2}
PRIORITY={
    "willingness_to_pay":10,
    "market_gap":20,
    "competitors":30,
    "pricing":40,
    "buyer":50,
}
QUERY_TEMPLATES={
    "buyer":[
        "{seed} developers teams customers users",
        "{seed} who uses workflow teams",
    ],
    "competitors":[
        "{seed} alternative competitor replacement vs",
        "{seed} alternatives gitpod cloud development",
    ],
    "pricing":[
        "{seed} pricing cost monthly per month",
        "{seed} price subscription plan cost",
    ],
    "willingness_to_pay":[
        "{seed} too expensive paid paying budget worth",
        "{seed} willing to pay cost expensive",
    ],
    "market_gap":[
        "{seed} problem need missing manual slow painful",
        "{seed} cannot support feature request workflow pain",
    ],
}
GOALS={
    "buyer":"找到明確的 Buyer / 使用者角色訊號。",
    "competitors":"找到替代品、競品、切換或比較訊號。",
    "pricing":"找到真實價格、訂閱週期或方案成本訊號。",
    "willingness_to_pay":"找到已付費、預算、太貴或明確願付訊號；必須跨至少兩個獨立 Domain。",
    "market_gap":"找到真實痛點、缺失功能、手工流程或未滿足需求；必須跨至少兩個獨立 Domain。",
}


def _status(value: str | None) -> str:
    normalized=(value or "UNKNOWN").upper().strip()
    return normalized if normalized in STATUS_RANK else "UNKNOWN"


def _dimension_state(snapshot: dict, name: str, current_status: str) -> dict:
    raw=((snapshot or {}).get("dimensions") or {}).get(name) or {}
    matches=raw.get("matches") or []
    domains=sorted({
        str(item.get("source_domain") or "").strip().lower()
        for item in matches
        if item.get("source_domain")
    })
    target=TARGET_STATUS[name]
    satisfied=STATUS_RANK[_status(current_status)]>=STATUS_RANK[target]
    target_sources=2 if target=="VALIDATED" else 1
    return {
        "status":_status(current_status),
        "target_status":target,
        "satisfied":satisfied,
        "independent_sources":len(domains),
        "target_independent_sources":target_sources,
        "source_gap":max(0,target_sources-len(domains)),
        "domains":domains,
        "evidence_count":int(raw.get("evidence_count") or len(matches)),
    }


def _queries(seed: str, dimension: str) -> list[str]:
    out=[]
    for template in QUERY_TEMPLATES[dimension]:
        q=" ".join(template.format(seed=seed).split())[:180]
        if q and q not in out:
            out.append(q)
    return out


def build_validation_search_plan(opportunity_id: UUID) -> dict:
    def _load() -> dict:
        with engine.connect() as db:
            row=db.execute(text("""
              SELECT o.id,o.title,o.canonical_title,o.asset_type,o.status,o.score,
                     o.evidence_gate_passed,o.build_readiness,
                     rv.validation_status,rv.buyer_status,rv.competitors_status,
                     rv.pricing_status,rv.willingness_to_pay_status,
                     rv.market_gap_status,rv.completeness_score,
                     rv.validation_gate_passed,rv.validation_snapshot,
                     rv.validator_version,rv.updated_at
              FROM digital_asset_opportunities o
              LEFT JOIN research_validations rv ON rv.opportunity_id=o.id
              WHERE o.id=:id
                AND COALESCE(o.title,'') NOT LIKE '[TEST_ONLY]%'
            """),{"id":opportunity_id}).mappings().one_or_none()
            if row is None:
                return {}
            evidence_domains=sorted({
                str(x[0]).strip().lower()
                for x in db.execute(text("""
                  SELECT DISTINCT lower(COALESCE(e.source_domain,''))
                  FROM opportunity_evidence oe
                  JOIN evidence e ON e.id=oe.evidence_id
                  WHERE oe.opportunity_id=:id
                    AND COALESCE(e.source_domain,'')<>''
                """),{"id":opportunity_id}).all()
                if x[0]
            })
            return {"row":dict(row),"evidence_domains":evidence_domains}

    loaded=read_with_retry(_load,operation="validation_search_plan")
    if not loaded:
        raise LookupError("Opportunity not found")

    row=loaded["row"]
    snapshot=row.get("validation_snapshot") or {}
    statuses={
        "buyer":row.get("buyer_status"),
        "competitors":row.get("competitors_status"),
        "pricing":row.get("pricing_status"),
        "willingness_to_pay":row.get("willingness_to_pay_status"),
        "market_gap":row.get("market_gap_status"),
    }
    dimensions={
        name:_dimension_state(snapshot,name,statuses[name])
        for name in TARGET_STATUS
    }
    seed=_search_seed(row)
    tasks=[
        {
            "task_id":f"{name.upper()}_VALIDATION",
            "dimension":name,
            "priority":PRIORITY[name],
            "label":name.replace("_"," ").title(),
            "current_status":dimensions[name]["status"],
            "target_status":dimensions[name]["target_status"],
            "current_domains":dimensions[name]["domains"],
            "source_gap":dimensions[name]["source_gap"],
            "queries":_queries(seed,name),
            "evidence_goal":GOALS[name],
            "acceptance":{
                "must_map_to_same_opportunity":True,
                "must_match_dimension_signal":True,
                "must_add_dimension_domain":True,
            },
        }
        for name in TARGET_STATUS
        if not dimensions[name]["satisfied"]
    ]
    tasks.sort(key=lambda x:x["priority"])

    eligible=bool(
        row.get("status")=="CANDIDATE"
        and row.get("evidence_gate_passed")
        and row.get("validation_status")=="CURRENT"
        and not row.get("validation_gate_passed")
        and tasks
    )
    reasons=[]
    if row.get("status")!="CANDIDATE":
        reasons.append("NOT_CANDIDATE")
    if not row.get("evidence_gate_passed"):
        reasons.append("EVIDENCE_GATE_NOT_PASSED")
    if row.get("validation_status")!="CURRENT":
        reasons.append("VALIDATION_NOT_CURRENT")
    if row.get("validation_gate_passed"):
        reasons.append("VALIDATION_ALREADY_PASSED")
    if not tasks:
        reasons.append("NO_VALIDATION_GAPS")

    return {
        "status":"ok",
        "contract":"VALIDATION_SEARCH_PLAN",
        "version":PLAN_VERSION,
        "mode":"PLAN_ONLY",
        "execution_enabled":False,
        "opportunity":{
            "id":str(row["id"]),
            "title":row["title"],
            "asset_type":row["asset_type"],
            "stage":row["status"],
            "score":float(row.get("score") or 0),
            "evidence_gate_passed":bool(row.get("evidence_gate_passed")),
            "build_readiness":row.get("build_readiness"),
        },
        "search_seed":seed,
        "validation":{
            "status":row.get("validation_status"),
            "completeness_score":float(row.get("completeness_score") or 0),
            "validation_gate_passed":bool(row.get("validation_gate_passed")),
            "validator_version":row.get("validator_version"),
            "dimensions":dimensions,
        },
        "existing_evidence_domains":loaded["evidence_domains"],
        "eligibility":{
            "recommended_for_expansion":eligible,
            "reasons":reasons,
        },
        "tasks":tasks,
        "completion_criteria":{
            "completeness_score":75.0,
            "willingness_to_pay":"VALIDATED",
            "market_gap":"VALIDATED",
            "buyer_minimum":"PARTIAL",
            "competitors_minimum":"PARTIAL",
            "pricing_minimum":"PARTIAL",
            "target_build_readiness":"BUILD_READY",
        },
        "safety":{
            "network_execution":False,
            "database_write":False,
            "automatic_ingest":False,
            "automatic_build_execution":False,
            "automatic_publish":False,
            "production_provider_writes":False,
        },
    }


def useful_validation_dimensions(plan: dict, source_domain: str, signals: dict) -> list[str]:
    domain=(source_domain or "").strip().lower()
    useful=[]
    dimensions=plan["validation"]["dimensions"]
    wanted={task["dimension"] for task in plan["tasks"]}
    for name in wanted:
        labels=signals.get(name) or []
        if not labels:
            continue
        if domain and domain in set(dimensions[name]["domains"]):
            continue
        useful.append(name)
    return sorted(useful,key=lambda name:PRIORITY[name])
