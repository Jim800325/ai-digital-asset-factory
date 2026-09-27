import json
from sqlalchemy import text

from app.db import engine

BUYER_HINTS = {
    "DATASET_API": "Potential buyer: developers, data teams, analysts, or products that need recurring structured data. Buyer is not yet independently validated.",
    "INTELLIGENCE_REPORT": "Potential buyer: operators, founders, analysts, or decision-makers who need recurring market intelligence. Buyer is not yet independently validated.",
    "MICRO_SAAS_TOOL": "Potential buyer: users repeatedly performing the detected workflow or pain point. Buyer is not yet independently validated.",
    "TEMPLATE_WORKFLOW": "Potential buyer: practitioners who repeatedly execute the detected workflow and value a reusable process. Buyer is not yet independently validated.",
    "CONTENT_IP": "Potential buyer: learners or practitioners seeking reusable specialist knowledge. Buyer is not yet independently validated.",
}

BUILD_COMPLEXITY = {
    "DATASET_API": "MEDIUM-HIGH — recurring acquisition, normalization, storage, freshness, and API reliability must be engineered.",
    "INTELLIGENCE_REPORT": "MEDIUM — source collection, synthesis, citation quality, and repeatable updates are the primary build concerns.",
    "MICRO_SAAS_TOOL": "HIGH — requires product UX, application logic, persistence, reliability, and ongoing operations.",
    "TEMPLATE_WORKFLOW": "LOW-MEDIUM — core asset creation is relatively simple, but validation, packaging, and maintenance still matter.",
    "CONTENT_IP": "MEDIUM — production is straightforward, but differentiation, evidence quality, updates, and distribution remain material.",
}

TYPE_RISKS = {
    "DATASET_API": "Data licensing, source stability, freshness, coverage gaps, and API operating cost.",
    "INTELLIGENCE_REPORT": "Weak differentiation, stale evidence, unsupported conclusions, and recurring research cost.",
    "MICRO_SAAS_TOOL": "Feature competition, support burden, reliability, acquisition cost, and insufficient willingness to pay.",
    "TEMPLATE_WORKFLOW": "Low defensibility, easy copying, weak repeat purchase, and limited willingness to pay.",
    "CONTENT_IP": "Low differentiation, rapid staleness, discoverability, and weak repeat purchase.",
}

def build_report_sections(opportunity: dict, evidence_rows: list[dict]) -> dict:
    sources=sorted({
        (row.get("source_domain") or row.get("source_url") or "").strip()
        for row in evidence_rows
        if row.get("source_domain") or row.get("source_url")
    })
    classes=sorted({row.get("source_class") or "unknown" for row in evidence_rows})
    top=sorted(
        evidence_rows,
        key=lambda row: (float(row.get("signal_strength") or 0), float(row.get("source_quality") or 0)),
        reverse=True,
    )[:5]

    evidence_lines=[]
    snapshot=[]
    for row in top:
        source=row.get("source_domain") or row.get("source_url") or "unknown"
        excerpt=(row.get("excerpt") or "").strip()[:500]
        evidence_lines.append(
            f"{source} [{row.get('source_class') or 'unknown'}] "
            f"signal={float(row.get('signal_strength') or 0):.2f}, "
            f"quality={float(row.get('source_quality') or 0):.2f}: {excerpt}"
        )
        snapshot.append({
            "source_url":row.get("source_url"),
            "source_domain":row.get("source_domain"),
            "source_class":row.get("source_class"),
            "source_quality":float(row.get("source_quality") or 0),
            "signal_strength":float(row.get("signal_strength") or 0),
            "excerpt":excerpt,
        })

    source_text=", ".join(sources) if sources else "none"
    class_text=", ".join(classes) if classes else "unknown"
    problem=(
        f"{opportunity['title']}. "
        f"Detected as {opportunity['asset_type']} with opportunity score "
        f"{float(opportunity['score']):.2f}. "
        "The precise customer problem remains subject to human validation against the cited evidence."
    )
    buyer=BUYER_HINTS.get(
        opportunity["asset_type"],
        "Potential buyer has not yet been independently validated from the available evidence."
    )
    alternatives=(
        "No specific existing alternative has been independently validated from the current evidence set. "
        f"Current evidence was observed on: {source_text}."
    )
    evidence=(
        f"{len(evidence_rows)} evidence items from {int(opportunity['independent_source_count'])} "
        f"independent sources across source classes: {class_text}. "
        f"Evidence quality={float(opportunity['evidence_quality_score']):.2f}, "
        f"source diversity={float(opportunity['source_diversity_score']):.2f}, "
        f"signal strength={float(opportunity['signal_strength_score']):.2f}. "
        + (" | ".join(evidence_lines) if evidence_lines else "No evidence excerpts available.")
    )
    monetization=(
        f"Current hypothesis: {opportunity['monetization_model']}. "
        "Pricing and willingness-to-pay have not yet been validated."
    )
    build_complexity=BUILD_COMPLEXITY.get(
        opportunity["asset_type"],
        "MEDIUM — implementation complexity requires separate technical validation."
    )
    risks=TYPE_RISKS.get(
        opportunity["asset_type"],
        "Demand, differentiation, operating cost, and willingness-to-pay remain unvalidated."
    )
    why_now=(
        f"Current evidence gate passed with {int(opportunity['independent_source_count'])} independent sources, "
        f"quality {float(opportunity['evidence_quality_score']):.2f}, diversity "
        f"{float(opportunity['source_diversity_score']):.2f}, and signal "
        f"{float(opportunity['signal_strength_score']):.2f}. "
        "This supports further research now; it does not by itself prove market demand or commercial viability."
    )
    return {
        "problem":problem,
        "buyer":buyer,
        "existing_alternatives":alternatives,
        "evidence":evidence,
        "monetization":monetization,
        "build_complexity":build_complexity,
        "risks":risks,
        "why_now":why_now,
        "evidence_snapshot":snapshot,
    }

def generate_research_report(opportunity_id):
    with engine.connect() as db:
        opportunity=db.execute(text("""
          SELECT id,title,asset_type,score,status,monetization_model,
                 independent_source_count,evidence_count,evidence_quality_score,
                 source_diversity_score,signal_strength_score,evidence_gate_passed
          FROM digital_asset_opportunities
          WHERE id=CAST(:id AS uuid)
        """),{"id":opportunity_id}).mappings().one_or_none()
        if not opportunity or opportunity["status"]!="CANDIDATE" or not opportunity["evidence_gate_passed"]:
            return None
        evidence_rows=[dict(row) for row in db.execute(text("""
          SELECT e.source_url,e.source_domain,e.source_class,e.source_quality,
                 e.signal_strength,e.excerpt,e.discovered_at
          FROM opportunity_evidence oe
          JOIN evidence e ON e.id=oe.evidence_id
          WHERE oe.opportunity_id=CAST(:id AS uuid)
          ORDER BY e.signal_strength DESC,e.source_quality DESC,e.discovered_at DESC
        """),{"id":opportunity_id}).mappings().all()]

    sections=build_report_sections(dict(opportunity),evidence_rows)
    snapshot=json.dumps(sections["evidence_snapshot"],ensure_ascii=False)
    with engine.begin() as db:
        report_id=db.execute(text("""
          INSERT INTO research_reports(
            opportunity_id,problem,buyer,existing_alternatives,evidence,
            monetization,build_complexity,risks,why_now,evidence_snapshot)
          VALUES(CAST(:opportunity_id AS uuid),:problem,:buyer,:existing_alternatives,:evidence,
                 :monetization,:build_complexity,:risks,:why_now,CAST(:snapshot AS jsonb))
          ON CONFLICT(opportunity_id) DO UPDATE SET
            report_status='GENERATED',
            problem=excluded.problem,buyer=excluded.buyer,
            existing_alternatives=excluded.existing_alternatives,evidence=excluded.evidence,
            monetization=excluded.monetization,build_complexity=excluded.build_complexity,
            risks=excluded.risks,why_now=excluded.why_now,
            evidence_snapshot=excluded.evidence_snapshot,
            generator_version=excluded.generator_version,observe_only=true,
            updated_at=now()
          RETURNING id
        """),{
            "opportunity_id":opportunity_id,
            "problem":sections["problem"],
            "buyer":sections["buyer"],
            "existing_alternatives":sections["existing_alternatives"],
            "evidence":sections["evidence"],
            "monetization":sections["monetization"],
            "build_complexity":sections["build_complexity"],
            "risks":sections["risks"],
            "why_now":sections["why_now"],
            "snapshot":snapshot,
        }).scalar_one()
    return {"report_id":str(report_id),"opportunity_id":str(opportunity_id),"observe_only":True}


def refresh_candidate_reports(limit: int = 100) -> int:
    with engine.connect() as db:
        ids=[row[0] for row in db.execute(text("""
          SELECT o.id
          FROM digital_asset_opportunities o
          LEFT JOIN research_reports rr ON rr.opportunity_id=o.id
          WHERE o.status='CANDIDATE'
            AND o.evidence_gate_passed=true
            AND (rr.id IS NULL OR o.updated_at>rr.updated_at)
          ORDER BY o.updated_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(limit,500))}).all()]
    generated=0
    for opportunity_id in ids:
        if generate_research_report(opportunity_id):
            generated+=1
    return generated


def mark_research_report_stale(opportunity_id) -> None:
    with engine.begin() as db:
        db.execute(text("""
          UPDATE research_reports
          SET report_status='STALE',updated_at=now()
          WHERE opportunity_id=CAST(:id AS uuid)
            AND report_status<>'STALE'
        """),{"id":opportunity_id})
