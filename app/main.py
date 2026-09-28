import secrets
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from redis import Redis
from rq import Queue
from sqlalchemy import text

from app.build_proposals import decide_build_proposal
from app.config import settings
from app.db import engine
from app.workers.pipeline import run_pipeline

app = FastAPI(title="AI Digital Asset Factory", version="0.3.0")

class BuildProposalDecision(BaseModel):
    decision: Literal["APPROVE","REJECT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(default="human-api",min_length=1,max_length=200)

def _require_approval_key(provided: str | None) -> None:
    expected=settings.human_approval_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Human approval gate is not configured",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(status_code=403,detail="Invalid human approval key")

@app.get("/health")
def health():
    with engine.connect() as conn:
        conn.execute(text("select 1"))
    return {
        "status":"ok",
        "mode":"OBSERVE",
        "approval_gate":"ENABLED" if settings.human_approval_key.strip() else "DISABLED",
        "build_execution":"DISABLED",
        "sandbox_execution":"ENABLED" if settings.sandbox_execution_enabled else "DISABLED",
        "openhands_adapter":"ENABLED" if settings.openhands_enabled else "DISABLED",
        "openhands_runtime":settings.openhands_runtime,
        "openhands_cli_version":settings.openhands_cli_version,
        "openhands_gateway_mode":settings.openhands_gateway_mode,
        "controlled_llm_proxy":"CONFIGURED" if (
            settings.openhands_gateway_mode.strip().upper()=="MOCK"
            or (
                bool(settings.openhands_allowed_model_list)
                and settings.openhands_max_requests>0
                and settings.openhands_max_total_tokens>0
                and settings.openhands_max_cost_usd>0
            )
        ) else "FAIL_CLOSED",
    }

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
             research_validation_score,build_readiness,build_proposal_status,
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
             o.build_readiness,o.research_validation_score,o.build_proposal_status,
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
             o.build_readiness,o.research_validation_score,o.build_proposal_status,
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
             o.build_proposal_status,
             rv.validation_status,rv.buyer_status,rv.competitors_status,rv.pricing_status,
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
             o.build_proposal_status,
             rv.validation_status,rv.buyer_status,rv.competitors_status,rv.pricing_status,
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

@app.get("/v1/build-proposals")
def build_proposals(limit: int = 50):
    sql=text("""
      SELECT bp.id,bp.opportunity_id,o.title AS opportunity_title,o.asset_type,
             o.build_readiness,bp.revision,bp.proposal_status,bp.title,bp.objective,
             bp.artifact_type,bp.proposed_stack,bp.requires_human_approval,
             bp.execution_enabled,bp.generator_version,bp.generated_at,bp.updated_at,
             bp.approved_at,bp.rejected_at
      FROM build_proposals bp
      JOIN digital_asset_opportunities o ON o.id=bp.opportunity_id
      ORDER BY bp.updated_at DESC
      LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"limit":min(max(limit,1),200)})]

@app.get("/v1/build-proposals/{proposal_id}")
def build_proposal(proposal_id: UUID):
    sql=text("""
      SELECT bp.*,o.title AS opportunity_title,o.asset_type,o.build_readiness,
             o.research_validation_score
      FROM build_proposals bp
      JOIN digital_asset_opportunities o ON o.id=bp.opportunity_id
      WHERE bp.id=:id
    """)
    with engine.connect() as conn:
        row=conn.execute(sql,{"id":proposal_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404,detail="Build proposal not found")
    return dict(row)

@app.get("/v1/build-proposals/{proposal_id}/decisions")
def build_proposal_decisions(proposal_id: UUID):
    sql=text("""
      SELECT id,proposal_id,proposal_revision,decision,reason,actor,decided_at
      FROM build_proposal_decisions
      WHERE proposal_id=:id
      ORDER BY decided_at DESC,id DESC
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"id":proposal_id})]

@app.post("/v1/build-proposals/{proposal_id}/decision")
def build_proposal_decision(
    proposal_id: UUID,
    payload: BuildProposalDecision,
    x_approval_key: str | None = Header(default=None,alias="X-Approval-Key"),
):
    _require_approval_key(x_approval_key)
    try:
        return decide_build_proposal(
            proposal_id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/sandbox-requests")
def sandbox_requests(limit: int = 50):
    sql=text("""
      SELECT sbr.id,sbr.proposal_id,sbr.proposal_revision,sbr.executor_kind,
             sbr.request_status,sbr.workspace_id,sbr.sandbox_image,
             sbr.network_policy,sbr.workspace_policy,sbr.external_side_effects,
             sbr.requested_by,sbr.requested_at,sbr.policy_checked_at,
             sbr.started_at,sbr.finished_at,sbr.error
      FROM sandbox_build_requests sbr
      ORDER BY sbr.requested_at DESC
      LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"limit":min(max(limit,1),200)})]

@app.get("/v1/sandbox-requests/{request_id}")
def sandbox_request(request_id: UUID):
    sql=text("""
      SELECT sbr.*,bp.opportunity_id,bp.proposal_status,bp.execution_enabled
      FROM sandbox_build_requests sbr
      JOIN build_proposals bp ON bp.id=sbr.proposal_id
      WHERE sbr.id=:id
    """)
    with engine.connect() as conn:
        row=conn.execute(sql,{"id":request_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404,detail="Sandbox request not found")
    return dict(row)

@app.get("/v1/sandbox-runs/{run_id}")
def sandbox_run(run_id: UUID):
    sql=text("""
      SELECT id,request_id,workspace_id,executor_kind,container_image,
             container_network,exit_code,stdout,stderr,started_at,finished_at
      FROM sandbox_runs
      WHERE id=:id
    """)
    with engine.connect() as conn:
        row=conn.execute(sql,{"id":run_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404,detail="Sandbox run not found")
    return dict(row)

@app.get("/v1/sandbox-runs/{run_id}/artifacts")
def sandbox_artifacts(run_id: UUID):
    sql=text("""
      SELECT id,run_id,relative_path,sha256,byte_size,media_type,captured_at
      FROM sandbox_artifacts
      WHERE run_id=:id
      ORDER BY relative_path
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"id":run_id})]

@app.get("/v1/sandbox-runs/{run_id}/tests")
def sandbox_tests(run_id: UUID):
    sql=text("""
      SELECT id,run_id,test_command,exit_code,stdout,stderr,passed,captured_at
      FROM sandbox_test_results
      WHERE run_id=:id
      ORDER BY captured_at,id
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"id":run_id})]


@app.get("/v1/openhands-executions")
def openhands_executions(limit: int = 50):
    sql=text("""
      SELECT oe.id,oe.request_id,oe.run_id,oe.cli_version,oe.model_name,
             oe.inner_runtime,oe.network_policy,oe.gateway_mode,oe.task_sha256,
             oe.budget_status,oe.gateway_request_count,oe.prompt_tokens,
             oe.completion_tokens,oe.total_tokens,oe.estimated_cost_usd,
             oe.blocked_reason,oe.live_model_verified,
             oe.exit_code,oe.started_at,oe.finished_at,
             sbr.request_status,sbr.workspace_id
      FROM openhands_executions oe
      JOIN sandbox_build_requests sbr ON sbr.id=oe.request_id
      ORDER BY oe.started_at DESC
      LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(sql,{"limit":min(max(limit,1),200)})]

@app.get("/v1/openhands-executions/{request_id}")
def openhands_execution(request_id: UUID):
    sql=text("""
      SELECT oe.id,oe.request_id,oe.run_id,oe.cli_version,oe.model_name,
             oe.inner_runtime,oe.network_policy,oe.gateway_mode,oe.task_sha256,
             oe.budget_status,oe.gateway_request_count,oe.prompt_tokens,
             oe.completion_tokens,oe.total_tokens,oe.estimated_cost_usd,
             oe.blocked_reason,oe.budget_snapshot,oe.live_model_verified,
             oe.exit_code,oe.trace_jsonl,oe.started_at,oe.finished_at,
             sbr.request_status,sbr.workspace_id,sbr.error
      FROM openhands_executions oe
      JOIN sandbox_build_requests sbr ON sbr.id=oe.request_id
      WHERE oe.request_id=:id
    """)
    with engine.connect() as conn:
        row=conn.execute(sql,{"id":request_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404,detail="OpenHands execution not found")
    return dict(row)
