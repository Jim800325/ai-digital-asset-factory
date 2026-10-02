import hashlib
import os
import secrets
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from redis import Redis
from rq import Queue
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.build_proposals import decide_build_proposal
from app.deployment_authorization import (
    create_deployment_plan,
    decide_deployment_authorization,
    get_deployment_plan,
    get_deployment_plan_for_candidate,
)
from app.production_release import (
    create_production_release_execution,
    decide_production_execution,
    get_production_release_execution,
    prepare_controlled_candidate,
    reconcile_controlled_prepare,
)
from app.config import settings
from app.db import database_selection, engine
from app.db_reliability import (
    DatabaseUnavailable,
    database_health,
    db_unavailable_payload,
    is_database_unavailable,
    read_with_retry,
)
from app.release_gate import decide_release_candidate, ensure_release_candidate
from app.release_integrity_gate import list_release_integrity_blocks
from app.release_review import ensure_release_review_package
from app.side_business_registry import (
    list_side_business_build_queue,
    list_side_business_providers,
    list_side_business_registry_runs,
    run_side_business_registry_cycle,
)
from app.review_ui import STATIC_DIR, router as review_ui_router
from app.review_workspace import get_review_workspace, list_review_workspace
from app.migrate import migrate, migration_files, migration_status
from app.live_acceptance_registry import (
    get_live_acceptance_audit,
    list_live_acceptance_audits,
    live_acceptance_evidence_index,
    live_acceptance_integrity_manifest,
)
from app.vercel_live_acceptance import (
    LiveAcceptanceError,
    _vercel_runtime_oidc_token,
    live_acceptance_db_diagnostics,
    run_vercel_live_acceptance,
)
from app.vercel_prepare_acceptance import (
    VercelPrepareAcceptanceError,
    acceptance_readiness as vercel_prepare_acceptance_readiness,
    authorize_prepare_acceptance,
    cleanup_prepare_acceptance,
    get_prepare_acceptance,
    reconcile_prepare_acceptance,
    recover_prepare_acceptance,
    start_prepare_acceptance,
)
from app.workers.pipeline import run_pipeline

app = FastAPI(title="AI Digital Asset Factory", version="0.3.0")

@app.on_event("startup")
def _apply_startup_migrations():
    migrate()

app.mount("/review-assets", StaticFiles(directory=STATIC_DIR), name="review-assets")
app.include_router(review_ui_router)


class BuildProposalDecision(BaseModel):
    decision: Literal["APPROVE","REJECT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(default="human-api",min_length=1,max_length=200)

class ReleaseDecision(BaseModel):
    decision: Literal["APPROVE","REJECT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(default="human-release-api",min_length=1,max_length=200)
    review_package_sha256: str | None = Field(default=None,min_length=64,max_length=64)

class DeploymentPlanRequest(BaseModel):
    target_project_id: str = Field(min_length=3,max_length=160)
    target_team_id: str | None = Field(default=None,max_length=160)
    actor: str = Field(default="human-deployment-api",min_length=1,max_length=200)

class DeploymentAuthorizationDecision(BaseModel):
    decision: Literal["AUTHORIZE","REJECT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(default="human-deployment-api",min_length=1,max_length=200)
    plan_sha256: str = Field(min_length=64,max_length=64)

class ProductionExecutionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["PROMOTE","ABORT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(
        default="human-production-execution-api",
        min_length=1,
        max_length=200,
    )
    execution_sha256: str = Field(min_length=64,max_length=64)

class ProductionExecutionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(
        default="human-production-execution-api",
        min_length=1,
        max_length=200,
    )

def _require_approval_key(provided: str | None) -> None:
    expected=settings.human_approval_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Human approval gate is not configured",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(status_code=403,detail="Invalid human approval key")

def _require_release_key(provided: str | None) -> None:
    expected=settings.human_release_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Human release gate is not configured",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(status_code=403,detail="Invalid human release key")

def _require_deployment_key(provided: str | None) -> None:
    expected=settings.human_deployment_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Deployment authorization gate is not configured",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(status_code=403,detail="Invalid deployment authorization key")


def _production_execution_key_independent() -> bool:
    expected=settings.human_production_execution_key.strip()
    if not expected:
        return False
    earlier_keys=(
        settings.human_approval_key.strip(),
        settings.human_release_key.strip(),
        settings.human_deployment_key.strip(),
    )
    return all(
        not value or not secrets.compare_digest(expected,value)
        for value in earlier_keys
    )

def _require_production_execution_key(provided: str | None) -> None:
    expected=settings.human_production_execution_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Human Production execution gate is not configured",
        )
    if not _production_execution_key_independent():
        raise HTTPException(
            status_code=503,
            detail="Human Production execution key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid human Production execution key",
        )


def _require_preview_acceptance_key(provided: str | None) -> str:
    vercel_env=(os.getenv("VERCEL_ENV") or "").strip().lower()
    if (
        vercel_env!="preview"
        or settings.deployment_authorization_preview_only is not True
    ):
        raise HTTPException(
            status_code=404,
            detail="Preview acceptance endpoint is unavailable",
        )
    expected=settings.preview_acceptance_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Preview acceptance key is not configured",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(status_code=403,detail="Invalid preview acceptance key")
    return expected

@app.get("/health")
def health():
    db_state=database_health()
    migrations = migration_status() if db_state["available"] else {
        "status": "DB_UNAVAILABLE",
        "expected_count": len(migration_files()),
        "applied_count": None,
        "latest_version": None,
        "pending": None,
    }
    payload={
        "status":"ok" if db_state["available"] else "degraded",
        "mode":"OBSERVE",
        "side_business_registry":"ENABLED" if settings.side_business_registry_enabled else "DISABLED",
        "database":db_state,
        "migrations":migrations,
        "approval_gate":"ENABLED" if settings.human_approval_key.strip() else "DISABLED",
        "release_gate":"ENABLED" if settings.human_release_key.strip() else "DISABLED",
        "deployment_authorization_gate":"ENABLED" if settings.human_deployment_key.strip() else "DISABLED",
        "production_execution_gate":(
            "DISABLED"
            if not settings.human_production_execution_key.strip()
            else (
                "ENABLED"
                if _production_execution_key_independent()
                else "MISCONFIGURED"
            )
        ),
        "preview_acceptance_bridge":"ENABLED" if (
            (os.getenv("VERCEL_ENV") or "").strip().lower()=="preview"
            and settings.preview_acceptance_key.strip()
        ) else "DISABLED",
        "controlled_production_release":"AUTHORIZATION_ONLY",
        "controlled_production_executor":"ENABLED" if settings.controlled_production_executor_enabled else "DISABLED",
        "production_promotion":"ENABLED" if settings.production_promotion_enabled else "DISABLED",
        "production_rollback":"ENABLED" if settings.production_rollback_enabled else "DISABLED",
        "production_execution_adapter":settings.production_execution_adapter.strip().upper() or "MOCK",
        "vercel_controlled_prepare":{
            "preview_only":settings.production_execution_preview_only,
            "token_present":bool(settings.vercel_controlled_executor_token.strip()),
            "allowed_project_count":len(
                settings.production_execution_allowed_project_id_list
            ),
            "allowed_team_count":len(
                settings.production_execution_allowed_team_id_list
            ),
            "real_project_denylisted":(
                "prj_orLCRCIm7aVfImH8ihB3gponFOEl"
                in settings.production_execution_denied_project_id_list
            ),
            "promotion_enabled":False,
            "rollback_enabled":False,
        },
        "deployment_executor":"DISABLED",
        "release_deployment":"DISABLED",
        "release_review_package":"ENABLED",
        "human_review_workspace":"ENABLED",
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
    if not db_state["available"]:
        payload["release_approval"]="APPROVAL_FAIL_CLOSED"
        return JSONResponse(status_code=503,content=payload)
    payload["release_approval"]="AVAILABLE"
    return payload

@app.post("/v1/runs", status_code=202)
def create_run():
    q=Queue("asset-factory",connection=Redis.from_url(settings.redis_url))
    job=q.enqueue(run_pipeline,job_timeout=900)
    return {"job_id":job.id,"status":"queued"}

@app.get("/v1/side-business/providers")
def side_business_providers(limit: int = 100, readiness: str | None = None):
    try:
        return list_side_business_providers(limit=limit,readiness=readiness)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc

@app.get("/v1/side-business/build-queue")
def side_business_build_queue(limit: int = 100):
    return list_side_business_build_queue(limit=limit)

@app.get("/v1/side-business/runs")
def side_business_registry_runs(limit: int = 30):
    return list_side_business_registry_runs(limit=limit)

@app.post("/v1/side-business/refresh", status_code=202)
def refresh_side_business_registry(
    x_approval_key: str | None = Header(default=None,alias="X-Approval-Key"),
):
    _require_approval_key(x_approval_key)
    q=Queue("asset-factory",connection=Redis.from_url(settings.redis_url))
    job=q.enqueue(run_side_business_registry_cycle,job_timeout=900)
    return {"job_id":job.id,"status":"queued","task":"side-business-registry"}

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


@app.post("/v1/release-candidates/from-request/{request_id}")
def create_release_candidate(request_id: UUID):
    try:
        return ensure_release_candidate(request_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc

@app.get("/v1/release-candidates")
def release_candidates(limit: int = 50):
    sql=text("""
      SELECT rc.id,rc.request_id,rc.run_id,rc.proposal_id,rc.proposal_revision,
             rc.release_status,rc.live_validation_required,
             rc.live_validation_verified,rc.artifact_manifest_sha256,
             rc.test_summary,rc.deployment_enabled,
             rc.created_at,rc.updated_at,rc.approved_at,rc.rejected_at
      FROM release_candidates rc
      ORDER BY rc.updated_at DESC
      LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(
            sql,{"limit":min(max(limit,1),200)}
        )]

@app.get("/v1/release-candidates/{candidate_id}")
def release_candidate(candidate_id: UUID):
    sql=text("""
      SELECT rc.*,
             bp.proposal_status,bp.execution_enabled,
             sbr.request_status,
             oe.gateway_mode,oe.budget_status,oe.live_model_verified
      FROM release_candidates rc
      JOIN build_proposals bp ON bp.id=rc.proposal_id
      JOIN sandbox_build_requests sbr ON sbr.id=rc.request_id
      LEFT JOIN openhands_executions oe ON oe.request_id=rc.request_id
      WHERE rc.id=:id
    """)
    with engine.connect() as conn:
        row=conn.execute(sql,{"id":candidate_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404,detail="Release candidate not found")
    return dict(row)

@app.get("/v1/release-candidates/{candidate_id}/decisions")
def release_candidate_decisions(candidate_id: UUID):
    sql=text("""
      SELECT id,release_candidate_id,candidate_status,
             decision,reason,actor,decided_at,
             review_package_id,review_package_sha256,source_tree_sha256
      FROM release_decisions
      WHERE release_candidate_id=:id
      ORDER BY decided_at DESC,id DESC
    """)
    def _load():
        with engine.connect() as conn:
            return [
                dict(r._mapping)
                for r in conn.execute(sql,{"id":candidate_id})
            ]
    try:
        return read_with_retry("release_decision_history",_load)
    except DatabaseUnavailable:
        return JSONResponse(
            status_code=503,
            content=db_unavailable_payload(
                operation="release_decision_history",
                approval_sensitive=False,
            ),
        )


@app.get("/v1/release-candidates/{candidate_id}/integrity-blocks")
def release_candidate_integrity_blocks(candidate_id: UUID):
    def _load():
        with engine.begin() as conn:
            return list_release_integrity_blocks(conn,candidate_id)
    try:
        return read_with_retry("release_integrity_block_history",_load)
    except DatabaseUnavailable:
        return JSONResponse(
            status_code=503,
            content=db_unavailable_payload(
                operation="release_integrity_block_history",
                approval_sensitive=False,
            ),
        )

@app.post("/v1/release-candidates/{candidate_id}/decision")
def release_candidate_decision(
    candidate_id: UUID,
    payload: ReleaseDecision,
    x_release_key: str | None = Header(default=None,alias="X-Release-Key"),
):
    _require_release_key(x_release_key)
    db_state=database_health()
    if not db_state["available"]:
        failure=db_unavailable_payload(
            operation="release_decision",
            approval_sensitive=True,
        )
        failure["database_state"]=db_state
        return JSONResponse(status_code=503,content=failure)
    try:
        return decide_release_candidate(
            candidate_id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
            review_package_sha256=payload.review_package_sha256,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except DatabaseUnavailable:
        return JSONResponse(
            status_code=503,
            content=db_unavailable_payload(
                operation="release_decision",
                approval_sensitive=True,
            ),
        )
    except DBAPIError as exc:
        if is_database_unavailable(exc):
            return JSONResponse(
                status_code=503,
                content=db_unavailable_payload(
                    operation="release_decision",
                    approval_sensitive=True,
                ),
            )
        return JSONResponse(
            status_code=409,
            content={
                "status":"RELEASE_CONSTRAINT_REJECTED",
                "detail":str(exc.orig) if getattr(exc,"orig",None) else str(exc),
                "deployment_enabled":False,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post("/v1/release-candidates/{candidate_id}/review-package")
def create_release_review_package(candidate_id: UUID):
    try:
        return ensure_release_review_package(candidate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc

@app.get("/v1/release-review-packages")
def release_review_packages(limit: int = 50):
    sql=text("""
      SELECT rrp.id,rrp.release_candidate_id,rrp.proposal_id,
             rrp.proposal_revision,rrp.run_id,rrp.baseline_package_id,
             rrp.package_status,rrp.content_snapshot_complete,
             rrp.source_tree_sha256,rrp.package_sha256,
             rrp.generator_version,rrp.generated_at,
             rc.release_status,rc.live_validation_verified,
             rc.deployment_enabled
      FROM release_review_packages rrp
      JOIN release_candidates rc ON rc.id=rrp.release_candidate_id
      ORDER BY rrp.generated_at DESC
      LIMIT :limit
    """)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(
            sql,{"limit":min(max(limit,1),200)}
        )]

@app.get("/v1/release-candidates/{candidate_id}/review-package")
def release_review_package(candidate_id: UUID):
    sql=text("""
      SELECT rrp.*,
             rc.release_status,rc.live_validation_verified,
             rc.deployment_enabled
      FROM release_review_packages rrp
      JOIN release_candidates rc ON rc.id=rrp.release_candidate_id
      WHERE rrp.release_candidate_id=:id
    """)
    with engine.connect() as conn:
        row=conn.execute(sql,{"id":candidate_id}).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404,detail="Release review package not found")
    return dict(row)


@app.post("/v1/release-candidates/{candidate_id}/deployment-plan")
def create_release_deployment_plan(
    candidate_id: UUID,
    payload: DeploymentPlanRequest,
    x_deployment_key: str | None = Header(default=None,alias="X-Deployment-Key"),
):
    _require_deployment_key(x_deployment_key)
    db_state=database_health()
    if not db_state["available"]:
        failure=db_unavailable_payload(
            operation="deployment_plan_create",
            approval_sensitive=True,
        )
        failure["database_state"]=db_state
        return JSONResponse(status_code=503,content=failure)
    try:
        return create_deployment_plan(
            candidate_id,
            target_project_id=payload.target_project_id,
            target_team_id=payload.target_team_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except DBAPIError as exc:
        if is_database_unavailable(exc):
            return JSONResponse(
                status_code=503,
                content=db_unavailable_payload(
                    operation="deployment_plan_create",
                    approval_sensitive=True,
                ),
            )
        return JSONResponse(
            status_code=409,
            content={
                "status":"DEPLOYMENT_CONSTRAINT_REJECTED",
                "detail":str(exc.orig) if getattr(exc,"orig",None) else str(exc),
                "execution_enabled":False,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/release-candidates/{candidate_id}/deployment-plan")
def release_deployment_plan(candidate_id: UUID):
    try:
        result=read_with_retry(
            "deployment_plan_read",
            lambda: get_deployment_plan_for_candidate(candidate_id),
        )
    except DatabaseUnavailable:
        return JSONResponse(
            status_code=503,
            content=db_unavailable_payload(
                operation="deployment_plan_read",
                approval_sensitive=False,
            ),
        )
    if result is None:
        raise HTTPException(status_code=404,detail="Deployment Plan not found")
    return result


@app.get("/v1/deployment-plans/{plan_id}")
def deployment_plan(plan_id: UUID):
    try:
        return read_with_retry(
            "deployment_plan_read",
            lambda: get_deployment_plan(plan_id),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except DatabaseUnavailable:
        return JSONResponse(
            status_code=503,
            content=db_unavailable_payload(
                operation="deployment_plan_read",
                approval_sensitive=False,
            ),
        )


@app.post("/v1/deployment-plans/{plan_id}/decision")
def deployment_plan_decision(
    plan_id: UUID,
    payload: DeploymentAuthorizationDecision,
    x_deployment_key: str | None = Header(default=None,alias="X-Deployment-Key"),
):
    _require_deployment_key(x_deployment_key)
    db_state=database_health()
    if not db_state["available"]:
        failure=db_unavailable_payload(
            operation="deployment_authorization_decision",
            approval_sensitive=True,
        )
        failure["database_state"]=db_state
        return JSONResponse(status_code=503,content=failure)
    try:
        return decide_deployment_authorization(
            plan_id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
            plan_sha256=payload.plan_sha256,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except DBAPIError as exc:
        if is_database_unavailable(exc):
            return JSONResponse(
                status_code=503,
                content=db_unavailable_payload(
                    operation="deployment_authorization_decision",
                    approval_sensitive=True,
                ),
            )
        return JSONResponse(
            status_code=409,
            content={
                "status":"DEPLOYMENT_CONSTRAINT_REJECTED",
                "detail":str(exc.orig) if getattr(exc,"orig",None) else str(exc),
                "execution_enabled":False,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post("/v1/deployment-plans/{plan_id}/execution")
def create_controlled_production_execution(
    plan_id: UUID,
    payload: ProductionExecutionAction,
    x_production_execution_key: str | None = Header(
        default=None,
        alias="X-Production-Execution-Key",
    ),
):
    _require_production_execution_key(x_production_execution_key)
    db_state=database_health()
    if not db_state["available"]:
        failure=db_unavailable_payload(
            operation="production_execution_snapshot_create",
            approval_sensitive=True,
        )
        failure["database_state"]=db_state
        failure["provider_write_performed"]=False
        failure["production_traffic_changed"]=False
        return JSONResponse(status_code=503,content=failure)
    try:
        return create_production_release_execution(
            plan_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except DBAPIError as exc:
        if is_database_unavailable(exc):
            failure=db_unavailable_payload(
                operation="production_execution_snapshot_create",
                approval_sensitive=True,
            )
            failure["provider_write_performed"]=False
            failure["production_traffic_changed"]=False
            return JSONResponse(status_code=503,content=failure)
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/production-release-executions/{execution_id}/prepare")
def prepare_controlled_production_execution(
    execution_id: UUID,
    payload: ProductionExecutionAction,
    x_production_execution_key: str | None = Header(
        default=None,
        alias="X-Production-Execution-Key",
    ),
):
    _require_production_execution_key(x_production_execution_key)
    db_state=database_health()
    if not db_state["available"]:
        failure=db_unavailable_payload(
            operation="production_execution_prepare",
            approval_sensitive=True,
        )
        failure["database_state"]=db_state
        failure["production_traffic_changed"]=False
        return JSONResponse(status_code=503,content=failure)
    try:
        return prepare_controlled_candidate(
            execution_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except DBAPIError as exc:
        if is_database_unavailable(exc):
            failure=db_unavailable_payload(
                operation="production_execution_prepare",
                approval_sensitive=True,
            )
            failure["production_traffic_changed"]=False
            return JSONResponse(status_code=503,content=failure)
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/production-release-executions/{execution_id}/reconcile")
def reconcile_controlled_production_execution(
    execution_id: UUID,
    payload: ProductionExecutionAction,
    x_production_execution_key: str | None = Header(
        default=None,
        alias="X-Production-Execution-Key",
    ),
):
    _require_production_execution_key(x_production_execution_key)
    db_state=database_health()
    if not db_state["available"]:
        failure=db_unavailable_payload(
            operation="production_execution_reconcile",
            approval_sensitive=True,
        )
        failure["database_state"]=db_state
        failure["provider_write_performed"]=False
        failure["production_traffic_changed"]=False
        return JSONResponse(status_code=503,content=failure)
    try:
        result=reconcile_controlled_prepare(
            execution_id,
            actor=payload.actor,
        )
        result["provider_write_performed"]=False
        result["production_traffic_changed"]=False
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except DBAPIError as exc:
        if is_database_unavailable(exc):
            failure=db_unavailable_payload(
                operation="production_execution_reconcile",
                approval_sensitive=True,
            )
            failure["provider_write_performed"]=False
            failure["production_traffic_changed"]=False
            return JSONResponse(status_code=503,content=failure)
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/production-release-executions/{execution_id}")
def production_release_execution(execution_id: UUID):
    try:
        return read_with_retry(
            "production_release_execution_read",
            lambda: get_production_release_execution(execution_id),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except DatabaseUnavailable:
        return JSONResponse(
            status_code=503,
            content=db_unavailable_payload(
                operation="production_release_execution_read",
                approval_sensitive=False,
            ),
        )


@app.post("/v1/production-release-executions/{execution_id}/decision")
def production_release_execution_decision(
    execution_id: UUID,
    payload: ProductionExecutionDecision,
    x_production_execution_key: str | None = Header(
        default=None,
        alias="X-Production-Execution-Key",
    ),
):
    _require_production_execution_key(x_production_execution_key)
    db_state=database_health()
    if not db_state["available"]:
        failure=db_unavailable_payload(
            operation="production_execution_decision",
            approval_sensitive=True,
        )
        failure["database_state"]=db_state
        failure["provider_write_performed"]=False
        failure["production_traffic_changed"]=False
        return JSONResponse(status_code=503,content=failure)
    try:
        return decide_production_execution(
            execution_id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
            execution_sha256=payload.execution_sha256,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except DBAPIError as exc:
        if is_database_unavailable(exc):
            failure=db_unavailable_payload(
                operation="production_execution_decision",
                approval_sensitive=True,
            )
            failure["provider_write_performed"]=False
            failure["production_traffic_changed"]=False
            return JSONResponse(status_code=503,content=failure)
        return JSONResponse(
            status_code=409,
            content={
                "status":"PRODUCTION_EXECUTION_CONSTRAINT_REJECTED",
                "detail":str(exc.orig) if getattr(exc,"orig",None) else str(exc),
                "provider_write_performed":False,
                "production_traffic_changed":False,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/review-workspace")
def review_workspace(limit: int = 50):
    try:
        return list_review_workspace(limit)
    except DatabaseUnavailable:
        return JSONResponse(
            status_code=503,
            content=db_unavailable_payload(
                operation="review_workspace_list",
                approval_sensitive=False,
            ),
        )

@app.get("/v1/review-workspace/{candidate_id}")
def review_workspace_candidate(candidate_id: UUID):
    try:
        return get_review_workspace(candidate_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except DatabaseUnavailable:
        return JSONResponse(
            status_code=503,
            content=db_unavailable_payload(
                operation="review_workspace_detail",
                approval_sensitive=False,
            ),
        )


@app.get("/v1/live-acceptance-audits")
def live_acceptance_audits(limit: int = 100):
    return list_live_acceptance_audits(limit)


@app.get("/v1/live-acceptance-audits/evidence-index")
def live_acceptance_audit_evidence_index():
    return live_acceptance_evidence_index()


@app.get("/v1/live-acceptance-audits/integrity-manifest")
def live_acceptance_audit_integrity_manifest():
    return live_acceptance_integrity_manifest()


@app.get("/v1/live-acceptance-audits/{audit_id}")
def live_acceptance_audit(audit_id: str):
    try:
        return get_live_acceptance_audit(audit_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.get(
    "/internal/vercel-prepare-acceptance/readiness",
    include_in_schema=False,
)
def internal_vercel_prepare_acceptance_readiness():
    return vercel_prepare_acceptance_readiness()


@app.get(
    "/internal/vercel-prepare-acceptance/{run_id}",
    include_in_schema=False,
)
def internal_vercel_prepare_acceptance_get(
    run_id: UUID,
    x_preview_acceptance_key: str | None = Header(
        default=None,
        alias="X-Preview-Acceptance-Key",
    ),
):
    _require_preview_acceptance_key(x_preview_acceptance_key)
    try:
        return get_prepare_acceptance(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.post(
    "/internal/vercel-prepare-acceptance/start",
    include_in_schema=False,
)
def internal_vercel_prepare_acceptance_start(
    x_preview_acceptance_key: str | None = Header(
        default=None,
        alias="X-Preview-Acceptance-Key",
    ),
    x_production_execution_key: str | None = Header(
        default=None,
        alias="X-Production-Execution-Key",
    ),
):
    _require_preview_acceptance_key(x_preview_acceptance_key)
    _require_production_execution_key(x_production_execution_key)
    try:
        return start_prepare_acceptance()
    except VercelPrepareAcceptanceError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/internal/vercel-prepare-acceptance/{run_id}/reconcile",
    include_in_schema=False,
)
def internal_vercel_prepare_acceptance_reconcile(
    run_id: UUID,
    x_preview_acceptance_key: str | None = Header(
        default=None,
        alias="X-Preview-Acceptance-Key",
    ),
    x_production_execution_key: str | None = Header(
        default=None,
        alias="X-Production-Execution-Key",
    ),
):
    _require_preview_acceptance_key(x_preview_acceptance_key)
    _require_production_execution_key(x_production_execution_key)
    try:
        return reconcile_prepare_acceptance(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except VercelPrepareAcceptanceError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get(
    "/internal/vercel-prepare-acceptance/{run_id}/recover-readonly",
    include_in_schema=False,
)
def internal_vercel_prepare_acceptance_recover_readonly(
    run_id: UUID,
    response: Response,
):
    """Preview-only, idempotent recovery for an already-consumed PREPARE write.

    This route intentionally accepts no caller-supplied provider target or
    provider credential and performs no provider mutation. The underlying
    recovery is limited to the narrowly fingerprinted Step 4A provider-ID
    mismatch case and uses Vercel GET requests only.
    """
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Robots-Tag"] = "noindex"
    try:
        result = recover_prepare_acceptance(run_id)
        result["provider_recovery_read_only"] = True
        result["provider_write_performed_by_recovery"] = False
        result["production_traffic_changed"] = False
        result["production_promotion_performed"] = False
        result["production_rollback_performed"] = False
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except VercelPrepareAcceptanceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/internal/vercel-prepare-acceptance/{run_id}/recover",
    include_in_schema=False,
)
def internal_vercel_prepare_acceptance_recover(
    run_id: UUID,
    x_preview_acceptance_key: str | None = Header(
        default=None,
        alias="X-Preview-Acceptance-Key",
    ),
    x_production_execution_key: str | None = Header(
        default=None,
        alias="X-Production-Execution-Key",
    ),
):
    _require_preview_acceptance_key(x_preview_acceptance_key)
    _require_production_execution_key(x_production_execution_key)
    try:
        result = recover_prepare_acceptance(run_id)
        result["provider_recovery_read_only"] = True
        result["production_traffic_changed"] = False
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except VercelPrepareAcceptanceError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/internal/vercel-prepare-acceptance/{run_id}/authorize",
    include_in_schema=False,
)
def internal_vercel_prepare_acceptance_authorize(
    run_id: UUID,
    x_preview_acceptance_key: str | None = Header(
        default=None,
        alias="X-Preview-Acceptance-Key",
    ),
    x_production_execution_key: str | None = Header(
        default=None,
        alias="X-Production-Execution-Key",
    ),
):
    _require_preview_acceptance_key(x_preview_acceptance_key)
    _require_production_execution_key(x_production_execution_key)
    try:
        return authorize_prepare_acceptance(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except VercelPrepareAcceptanceError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/internal/vercel-prepare-acceptance/{run_id}/cleanup",
    include_in_schema=False,
)
def internal_vercel_prepare_acceptance_cleanup(
    run_id: UUID,
    x_preview_acceptance_key: str | None = Header(
        default=None,
        alias="X-Preview-Acceptance-Key",
    ),
    x_production_execution_key: str | None = Header(
        default=None,
        alias="X-Production-Execution-Key",
    ),
):
    _require_preview_acceptance_key(x_preview_acceptance_key)
    _require_production_execution_key(x_production_execution_key)
    try:
        return cleanup_prepare_acceptance(run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except VercelPrepareAcceptanceError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get(
    "/internal/preview-live-acceptance/readiness",
    include_in_schema=False,
)
def internal_preview_live_acceptance_readiness():
    vercel_env=(os.getenv("VERCEL_ENV") or "").strip().lower()
    if (
        vercel_env!="preview"
        or settings.deployment_authorization_preview_only is not True
    ):
        raise HTTPException(
            status_code=404,
            detail="Preview readiness endpoint is unavailable",
        )

    try:
        selected=database_selection()
    except RuntimeError as exc:
        raise HTTPException(status_code=503,detail=str(exc)) from exc

    db_state=database_health()
    migrations=(
        migration_status()
        if db_state["available"]
        else {
            "status":"DB_UNAVAILABLE",
            "expected_count":len(migration_files()),
            "applied_count":None,
            "latest_version":None,
            "pending":None,
        }
    )

    preview_acceptance_key_present=bool(
        settings.preview_acceptance_key.strip()
    )
    aihubmix_api_key_present=bool(
        (os.getenv("AIHUBMIX_API_KEY") or "").strip()
    )
    human_release_key_present=bool(
        settings.human_release_key.strip()
    )
    human_deployment_key_present=bool(
        settings.human_deployment_key.strip()
    )
    vercel_oidc_token_present=bool(
        _vercel_runtime_oidc_token()
    )

    ready=(
        db_state["available"]
        and migrations.get("status")=="CURRENT"
        and selected.get("source")=="PREVIEW_DATABASE_URL"
        and selected.get("preview_isolated") is True
        and preview_acceptance_key_present
        and aihubmix_api_key_present
        and human_release_key_present
        and human_deployment_key_present
        and vercel_oidc_token_present
    )

    return {
        "status":"READY" if ready else "NOT_READY",
        "vercel_env":"preview",
        "database_source":selected.get("source"),
        "preview_isolated":bool(selected.get("preview_isolated")),
        "database_available":bool(db_state.get("available")),
        "migrations":migrations,
        "preview_acceptance_key_present":preview_acceptance_key_present,
        "aihubmix_api_key_present":aihubmix_api_key_present,
        "human_release_key_present":human_release_key_present,
        "human_deployment_key_present":human_deployment_key_present,
        "vercel_oidc_token_present":vercel_oidc_token_present,
        "live_model_invoked":False,
        "deployment_executor":"DISABLED",
        "deployment_enabled":False,
        "execution_enabled":False,
        "production_deployment_executed":False,
    }


@app.get(
    "/internal/preview-live-acceptance/preflight",
    include_in_schema=False,
)
def internal_preview_live_acceptance_preflight(
    x_preview_acceptance_key: str | None = Header(
        default=None,
        alias="X-Preview-Acceptance-Key",
    ),
):
    _require_preview_acceptance_key(x_preview_acceptance_key)
    try:
        selected=database_selection()
    except RuntimeError as exc:
        raise HTTPException(status_code=503,detail=str(exc)) from exc

    db_state=database_health()
    migrations=(
        migration_status()
        if db_state["available"]
        else {
            "status":"DB_UNAVAILABLE",
            "expected_count":len(migration_files()),
            "applied_count":None,
            "latest_version":None,
            "pending":None,
        }
    )
    return {
        "status":"READY" if (
            db_state["available"]
            and migrations.get("status")=="CURRENT"
            and selected.get("source")=="PREVIEW_DATABASE_URL"
            and selected.get("preview_isolated") is True
            and bool((os.getenv("AIHUBMIX_API_KEY") or "").strip())
        ) else "NOT_READY",
        "vercel_env":selected.get("vercel_env"),
        "database_source":selected.get("source"),
        "preview_isolated":bool(selected.get("preview_isolated")),
        "database_available":bool(db_state.get("available")),
        "migrations":migrations,
        "aihubmix_api_key_present":bool(
            (os.getenv("AIHUBMIX_API_KEY") or "").strip()
        ),
        "live_model_invoked":False,
        "deployment_executor":"DISABLED",
        "deployment_enabled":False,
        "execution_enabled":False,
        "production_deployment_executed":False,
    }




def _preview_acceptance_attempt_digest(expected: str) -> str:
    source_commit=(os.getenv("VERCEL_GIT_COMMIT_SHA") or "").strip().lower()
    if (
        len(source_commit)!=40
        or any(ch not in "0123456789abcdef" for ch in source_commit)
    ):
        raise RuntimeError(
            "Preview acceptance requires a full VERCEL_GIT_COMMIT_SHA"
        )
    return hashlib.sha256(
        (expected+"\n"+source_commit).encode("utf-8")
    ).hexdigest()


@app.post(
    "/internal/preview-live-acceptance",
    include_in_schema=False,
)
def internal_preview_live_acceptance(
    request: Request,
    x_preview_acceptance_key: str | None = Header(
        default=None,
        alias="X-Preview-Acceptance-Key",
    ),
):
    expected=_require_preview_acceptance_key(x_preview_acceptance_key)
    try:
        digest=_preview_acceptance_attempt_digest(expected)
    except RuntimeError as exc:
        raise HTTPException(status_code=503,detail=str(exc)) from exc
    try:
        result=run_vercel_live_acceptance(
            "",
            vercel_oidc_token=request.headers.get("x-vercel-oidc-token"),
            preverified_digest=digest,
            persist_preview_fixture=True,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except LiveAcceptanceError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc

    if result.get("acceptance_status")!="PASSED":
        return JSONResponse(status_code=409,content=result)
    return result


@app.get(
    "/internal/live-acceptance/{trigger_token}/db-diagnostics",
    include_in_schema=False,
)
def internal_live_acceptance_db_diagnostics(trigger_token: str):
    try:
        return live_acceptance_db_diagnostics(trigger_token)
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc


@app.get("/internal/live-acceptance/{trigger_token}", include_in_schema=False)
def internal_live_acceptance(trigger_token: str, request: Request):
    try:
        return run_vercel_live_acceptance(
            trigger_token,
            vercel_oidc_token=request.headers.get("x-vercel-oidc-token"),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except LiveAcceptanceError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
