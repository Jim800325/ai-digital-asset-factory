from uuid import UUID

from fastapi import FastAPI, HTTPException
from redis import Redis
from rq import Queue
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.workers.pipeline import run_pipeline

app = FastAPI(title="AI Digital Asset Factory", version="0.2.0")

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("select 1"))
    return {"status":"ok","mode":"OBSERVE"}

@app.post("/v1/runs", status_code=202)
def create_run():
    q=Queue("asset-factory",connection=Redis.from_url(settings.redis_url))
    job=q.enqueue(run_pipeline,job_timeout=900)
    return {"job_id":job.id,"status":"queued"}

@app.get("/v1/runs")
def runs(limit: int = 30):
    sql=text("""
      SELECT id,status,pages_discovered,pages_crawled,evidence_created,
             opportunities_created,error,started_at,finished_at
      FROM pipeline_runs
      ORDER BY started_at DESC
      LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"limit":min(max(limit,1),200)})]

@app.get("/v1/opportunities")
def opportunities(limit: int = 50):
    sql=text("""
      SELECT id,title,asset_type,score,demand_score,repeatability_score,
             automation_score,ownership_score,marginal_cost_score,evidence_score,
             repeatable_sale,update_automation,status,independent_source_count,evidence_count,
             evidence_quality_score,source_diversity_score,signal_strength_score,evidence_gate_passed,
             research_validation_score,build_readiness,
             cluster_confidence,canonical_title,monetization_model,source_url,created_at,updated_at
      FROM digital_asset_opportunities
      ORDER BY score DESC,created_at DESC
      LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"limit":min(max(limit,1),200)})]

@app.get("/v1/opportunities/{opportunity_id}/evidence")
def opportunity_evidence(opportunity_id: UUID):
    sql=text("""
      SELECT e.id,e.signal_type,e.excerpt,e.source_url,e.source_domain,
             e.source_class,e.source_quality,e.signal_strength,
             e.confidence,e.discovered_at,e.last_seen_at,
             d.title AS document_title
      FROM opportunity_evidence oe
      JOIN evidence e ON e.id=oe.evidence_id
      LEFT JOIN documents d ON d.id=e.document_id
      WHERE oe.opportunity_id=:id
      ORDER BY e.discovered_at DESC,e.id
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"id":opportunity_id})]

@app.get("/v1/research-reports")
def research_reports(limit: int = 50):
    sql=text("""
      SELECT rr.id,rr.opportunity_id,o.title,o.asset_type,o.score,o.status,
             o.build_readiness,o.research_validation_score,
             rr.report_status,rr.problem,rr.buyer,rr.existing_alternatives,
             rr.evidence,rr.monetization,rr.build_complexity,rr.risks,rr.why_now,
             rr.generator_version,rr.observe_only,rr.generated_at,rr.updated_at
      FROM research_reports rr
      JOIN digital_asset_opportunities o ON o.id=rr.opportunity_id
      ORDER BY rr.updated_at DESC
      LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"limit":min(max(limit,1),200)})]

@app.get("/v1/research-reports/{opportunity_id}")
def research_report(opportunity_id: UUID):
    sql=text("""
      SELECT rr.id,rr.opportunity_id,o.title,o.asset_type,o.score,o.status,
             o.build_readiness,o.research_validation_score,
             rr.report_status,rr.problem,rr.buyer,rr.existing_alternatives,
             rr.evidence,rr.monetization,rr.build_complexity,rr.risks,rr.why_now,
             rr.evidence_snapshot,rr.generator_version,rr.observe_only,
             rr.generated_at,rr.updated_at
      FROM research_reports rr
      JOIN digital_asset_opportunities o ON o.id=rr.opportunity_id
      WHERE rr.opportunity_id=:id
    """)
    with engine.connect() as conn:
        row=conn.execute(sql,{"id":opportunity_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404,detail="Research report not found")
    return dict(row)

@app.get("/v1/research-validations")
def research_validations(limit: int = 50):
    sql=text("""
      SELECT rv.id,rv.opportunity_id,o.title,o.asset_type,o.score,o.status,
             rv.buyer_status,rv.competitors_status,rv.pricing_status,
             rv.willingness_to_pay_status,rv.market_gap_status,
             rv.completeness_score,rv.validation_gate_passed,rv.build_readiness,
             rv.validator_version,rv.observe_only,rv.validated_at,rv.updated_at
      FROM research_validations rv
      JOIN digital_asset_opportunities o ON o.id=rv.opportunity_id
      ORDER BY rv.completeness_score DESC,rv.updated_at DESC
      LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"limit":min(max(limit,1),200)})]

@app.get("/v1/research-validations/{opportunity_id}")
def research_validation(opportunity_id: UUID):
    sql=text("""
      SELECT rv.id,rv.opportunity_id,o.title,o.asset_type,o.score,o.status,
             rv.buyer_status,rv.competitors_status,rv.pricing_status,
             rv.willingness_to_pay_status,rv.market_gap_status,
             rv.completeness_score,rv.validation_gate_passed,rv.build_readiness,
             rv.validation_snapshot,rv.validator_version,rv.observe_only,
             rv.validated_at,rv.updated_at
      FROM research_validations rv
      JOIN digital_asset_opportunities o ON o.id=rv.opportunity_id
      WHERE rv.opportunity_id=:id
    """)
    with engine.connect() as conn:
        row=conn.execute(sql,{"id":opportunity_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404,detail="Research validation not found")
    return dict(row)
