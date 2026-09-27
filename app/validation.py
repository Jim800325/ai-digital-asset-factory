import json
import re

from sqlalchemy import text

from app.db import engine
from app.evidence_quality import normalized_domain

VALIDATOR_VERSION = "validation-v0.2-deterministic"

STATUS_FACTOR = {"UNKNOWN": 0.0, "PARTIAL": 0.5, "VALIDATED": 1.0}
DIMENSION_WEIGHTS = {
    "buyer": 15.0,
    "competitors": 15.0,
    "pricing": 20.0,
    "willingness_to_pay": 25.0,
    "market_gap": 25.0,
}

STRONG_LABELS = {
    "pricing": {"currency_amount", "billing_period"},
}

PATTERNS = {
    "buyer": [
        ("buyer_role", re.compile(r"\b(customers?|users?|developers?|teams?|companies|businesses|founders?|analysts?|agencies|marketers?|creators?|sellers?|operators?)\b", re.I)),
    ],
    "competitors": [
        ("alternative", re.compile(r"\b(alternatives?|competitors?|replacement|replace|switch(?:ed|ing)?\s+from|versus|vs\.?)\b", re.I)),
    ],
    "pricing": [
        ("currency_amount", re.compile(r"(?:[$€£]\s?\d+(?:\.\d{1,2})?|\b(?:usd|eur|gbp)\s?\d+(?:\.\d{1,2})?)", re.I)),
        ("billing_period", re.compile(r"\b\d+(?:\.\d{1,2})?\s*(?:/\s*(?:mo|month|yr|year)|per\s+(?:month|year)|monthly|yearly)\b", re.I)),
        ("pricing_term", re.compile(r"\b(pricing|price|cost|subscription|plan)\b", re.I)),
    ],
    "willingness_to_pay": [
        ("willing_to_pay", re.compile(r"\b(willing\s+to\s+pay|would\s+pay|can\s+pay|budget)\b", re.I)),
        ("active_payment", re.compile(r"\b(?:we|i|our\s+team|customers?)\s+(?:currently\s+)?pay\b", re.I)),
        ("paid_signal", re.compile(r"\b(paid|paying|subscribe|subscribed|too\s+expensive|worth\s+(?:it|[$€£]))\b", re.I)),
    ],
    "market_gap": [
        ("manual_pain", re.compile(r"\b(manual|manually|time[- ]consuming|hours?\s+to|too\s+slow|difficult|painful)\b", re.I)),
        ("unmet_need", re.compile(r"\b(need|wish|missing|feature\s+request|no\s+way\s+to|cannot|can't|doesn't\s+support|problem)\b", re.I)),
    ],
}

def _match_dimension(rows: list[dict], dimension: str) -> dict:
    matches=[]
    domains=set()
    strong_domains=set()
    patterns=PATTERNS[dimension]
    for row in rows:
        if float(row.get("source_quality") or 0) < 50:
            continue
        if float(row.get("signal_strength") or 0) < 45:
            continue
        material=((row.get("content") or "") + " " + (row.get("excerpt") or ""))[:12000]
        labels=[label for label,pattern in patterns if pattern.search(material)]
        if not labels:
            continue
        domain=normalized_domain(row.get("source_url") or "") or (row.get("source_domain") or "").lower()
        if domain:
            domains.add(domain)
            strong_labels=STRONG_LABELS.get(dimension)
            if not strong_labels or strong_labels.intersection(labels):
                strong_domains.add(domain)
        matches.append({
            "source_url":row.get("source_url"),
            "source_domain":domain,
            "source_class":row.get("source_class"),
            "source_quality":float(row.get("source_quality") or 0),
            "signal_strength":float(row.get("signal_strength") or 0),
            "matched_signals":labels,
            "excerpt":(row.get("excerpt") or "").strip()[:500],
        })

    if len(domains) >= 2 and len(matches) >= 2 and len(strong_domains) >= 2:
        status="VALIDATED"
    elif matches:
        status="PARTIAL"
    else:
        status="UNKNOWN"
    return {
        "status":status,
        "independent_sources":len(domains),
        "evidence_count":len(matches),
        "matches":matches[:8],
    }

def build_validation(evidence_rows: list[dict]) -> dict:
    dimensions={
        "buyer":_match_dimension(evidence_rows,"buyer"),
        "competitors":_match_dimension(evidence_rows,"competitors"),
        "pricing":_match_dimension(evidence_rows,"pricing"),
        "willingness_to_pay":_match_dimension(evidence_rows,"willingness_to_pay"),
        "market_gap":_match_dimension(evidence_rows,"market_gap"),
    }
    score=round(sum(
        DIMENSION_WEIGHTS[name] * STATUS_FACTOR[result["status"]]
        for name,result in dimensions.items()
    ),2)
    return {"dimensions":dimensions,"completeness_score":score}

def build_ready_for(opportunity: dict, result: dict) -> bool:
    d=result["dimensions"]
    return (
        opportunity["status"]=="CANDIDATE"
        and bool(opportunity["evidence_gate_passed"])
        and result["completeness_score"] >= 75
        and d["buyer"]["status"] != "UNKNOWN"
        and d["competitors"]["status"] != "UNKNOWN"
        and d["pricing"]["status"] != "UNKNOWN"
        and d["willingness_to_pay"]["status"] == "VALIDATED"
        and d["market_gap"]["status"] == "VALIDATED"
    )

def validate_research(opportunity_id):
    with engine.connect() as db:
        opportunity=db.execute(text("""
          SELECT id,status,evidence_gate_passed
          FROM digital_asset_opportunities
          WHERE id=CAST(:id AS uuid)
        """),{"id":opportunity_id}).mappings().one_or_none()
        if not opportunity:
            return None
        if opportunity["status"]!="CANDIDATE" or not opportunity["evidence_gate_passed"]:
            mark_validation_not_ready(opportunity_id)
            return None

        rows=[dict(row) for row in db.execute(text("""
          SELECT e.id,e.source_url,e.source_domain,e.source_class,
                 e.source_quality,e.signal_strength,e.excerpt,
                 d.content
          FROM opportunity_evidence oe
          JOIN evidence e ON e.id=oe.evidence_id
          JOIN documents d ON d.id=e.document_id
          WHERE oe.opportunity_id=CAST(:id AS uuid)
          ORDER BY e.signal_strength DESC,e.source_quality DESC
        """),{"id":opportunity_id}).mappings().all()]

    result=build_validation(rows)
    ready=build_ready_for(dict(opportunity),result)
    readiness="BUILD_READY" if ready else "NOT_READY"
    dims=result["dimensions"]
    snapshot=json.dumps(result,ensure_ascii=False)

    with engine.begin() as db:
        validation_id=db.execute(text("""
          INSERT INTO research_validations(
            opportunity_id,buyer_status,competitors_status,pricing_status,
            willingness_to_pay_status,market_gap_status,completeness_score,
            validation_gate_passed,build_readiness,validation_snapshot,validator_version)
          VALUES(
            CAST(:id AS uuid),:buyer,:competitors,:pricing,:wtp,:gap,:score,
            :gate,:readiness,CAST(:snapshot AS jsonb),:validator_version)
          ON CONFLICT(opportunity_id) DO UPDATE SET
            buyer_status=excluded.buyer_status,
            competitors_status=excluded.competitors_status,
            pricing_status=excluded.pricing_status,
            willingness_to_pay_status=excluded.willingness_to_pay_status,
            market_gap_status=excluded.market_gap_status,
            completeness_score=excluded.completeness_score,
            validation_gate_passed=excluded.validation_gate_passed,
            build_readiness=excluded.build_readiness,
            validation_snapshot=excluded.validation_snapshot,
            validator_version=excluded.validator_version,
            observe_only=true,
            validated_at=now(),
            updated_at=now()
          RETURNING id
        """),{
            "id":opportunity_id,
            "buyer":dims["buyer"]["status"],
            "competitors":dims["competitors"]["status"],
            "pricing":dims["pricing"]["status"],
            "wtp":dims["willingness_to_pay"]["status"],
            "gap":dims["market_gap"]["status"],
            "score":result["completeness_score"],
            "gate":ready,
            "readiness":readiness,
            "snapshot":snapshot,
            "validator_version":VALIDATOR_VERSION,
        }).scalar_one()
        db.execute(text("""
          UPDATE digital_asset_opportunities
          SET research_validation_score=:score,
              build_readiness=:readiness
          WHERE id=CAST(:id AS uuid)
        """),{
            "score":result["completeness_score"],
            "readiness":readiness,
            "id":opportunity_id,
        })

    return {
        "validation_id":str(validation_id),
        "opportunity_id":str(opportunity_id),
        "buyer_status":dims["buyer"]["status"],
        "competitors_status":dims["competitors"]["status"],
        "pricing_status":dims["pricing"]["status"],
        "willingness_to_pay_status":dims["willingness_to_pay"]["status"],
        "market_gap_status":dims["market_gap"]["status"],
        "completeness_score":result["completeness_score"],
        "validation_gate_passed":ready,
        "build_readiness":readiness,
        "observe_only":True,
    }

def mark_validation_not_ready(opportunity_id) -> None:
    with engine.begin() as db:
        db.execute(text("""
          UPDATE research_validations
          SET validation_gate_passed=false,
              build_readiness='NOT_READY',
              updated_at=now()
          WHERE opportunity_id=CAST(:id AS uuid)
        """),{"id":opportunity_id})
        db.execute(text("""
          UPDATE digital_asset_opportunities
          SET build_readiness='NOT_READY'
          WHERE id=CAST(:id AS uuid)
            AND build_readiness<>'NOT_READY'
        """),{"id":opportunity_id})

def refresh_candidate_validations(limit: int = 100) -> int:
    with engine.begin() as db:
        db.execute(text("""
          UPDATE research_validations rv
          SET validation_gate_passed=false,
              build_readiness='NOT_READY',
              updated_at=now()
          FROM digital_asset_opportunities o
          WHERE rv.opportunity_id=o.id
            AND (o.status<>'CANDIDATE' OR o.evidence_gate_passed=false)
            AND rv.build_readiness<>'NOT_READY'
        """))
        db.execute(text("""
          UPDATE digital_asset_opportunities
          SET build_readiness='NOT_READY'
          WHERE (status<>'CANDIDATE' OR evidence_gate_passed=false)
            AND build_readiness<>'NOT_READY'
        """))
        ids=[row[0] for row in db.execute(text("""
          SELECT o.id
          FROM digital_asset_opportunities o
          JOIN research_reports rr ON rr.opportunity_id=o.id
          LEFT JOIN research_validations rv ON rv.opportunity_id=o.id
          WHERE o.status='CANDIDATE'
            AND o.evidence_gate_passed=true
            AND rr.report_status='GENERATED'
            AND (
              rv.id IS NULL
              OR rv.validator_version<>:validator_version
            )
          ORDER BY o.updated_at DESC
          LIMIT :limit
        """),{
            "limit":max(1,min(limit,500)),
            "validator_version":VALIDATOR_VERSION,
        }).all()]

    validated=0
    for opportunity_id in ids:
        if validate_research(opportunity_id):
            validated+=1
    return validated
