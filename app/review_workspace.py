from uuid import UUID

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.db_reliability import read_with_retry
from app.release_integrity_gate import (
    evaluate_release_integrity,
    list_release_integrity_blocks,
)


def list_review_workspace(limit:int=50)->list[dict]:
    sql=text("""
      SELECT rc.id AS release_candidate_id,
             rc.release_status,
             rc.live_validation_verified,
             rc.live_validation_required,
             rc.deployment_enabled,
             rc.updated_at,
             bp.id AS proposal_id,
             bp.revision AS proposal_revision,
             bp.title AS proposal_title,
             bp.artifact_type,
             o.id AS opportunity_id,
             o.title AS opportunity_title,
             o.asset_type,
             sbr.request_status,
             oe.gateway_mode,
             oe.budget_status,
             oe.live_model_verified,
             rrp.id AS review_package_id,
             rrp.package_status,
             rrp.content_snapshot_complete,
             rrp.source_tree_sha256,
             rrp.package_sha256,
             rrp.artifact_manifest,
             rrp.risk_summary->>'risk_level' AS risk_level,
             COALESCE((rrp.test_report->>'passed')::int,0) AS passed_tests,
             COALESCE((rrp.test_report->>'failed')::int,0) AS failed_tests,
             jsonb_array_length(rrp.artifact_manifest) AS artifact_count,
             jsonb_array_length(rrp.dependency_inventory) AS dependency_count
      FROM release_candidates rc
      JOIN build_proposals bp ON bp.id=rc.proposal_id
      JOIN digital_asset_opportunities o ON o.id=bp.opportunity_id
      JOIN sandbox_build_requests sbr ON sbr.id=rc.request_id
      LEFT JOIN openhands_executions oe ON oe.request_id=rc.request_id
      LEFT JOIN release_review_packages rrp ON rrp.release_candidate_id=rc.id
      WHERE rc.archived_at IS NULL
      ORDER BY rc.updated_at DESC,rc.id DESC
      LIMIT :limit
    """)
    def _load_rows():
        with engine.connect() as db:
            return db.execute(
                sql,
                {"limit":min(max(limit,1),200)},
            ).mappings().all()
    rows=read_with_retry("review_workspace_list",_load_rows)
    result=[]
    for row in rows:
        item=dict(row)
        gate=evaluate_release_integrity(
            item.get("source_tree_sha256"),
            list(item.get("artifact_manifest") or []),
        )
        item["integrity_status"]=gate["integrity_status"]
        item["integrity_gate_allowed"]=gate["allowed"]
        item["integrity_audit_id"]=gate.get("audit_id")
        item["integrity_blocking_reasons"]=gate.get("blocking_reasons") or []
        result.append(item)
    return result


def get_review_workspace(candidate_id:UUID)->dict:
    sql=text("""
      SELECT rc.id AS release_candidate_id,
             rc.request_id,
             rc.run_id,
             rc.proposal_id,
             rc.proposal_revision,
             rc.source_fingerprint,
             rc.release_status,
             rc.live_validation_required,
             rc.live_validation_verified,
             rc.artifact_manifest_sha256,
             rc.test_summary AS candidate_test_summary,
             rc.deployment_enabled,
             rc.created_at AS candidate_created_at,
             rc.updated_at AS candidate_updated_at,
             rc.approved_at,
             rc.rejected_at,
             bp.title AS proposal_title,
             bp.objective,
             bp.artifact_type,
             bp.scope AS proposal_scope,
             bp.success_criteria,
             bp.constraints,
             bp.proposed_stack,
             bp.proposal_status,
             bp.execution_enabled,
             o.id AS opportunity_id,
             o.title AS opportunity_title,
             o.asset_type,
             o.score AS opportunity_score,
             o.build_readiness,
             sbr.request_status,
             sbr.executor_kind,
             sbr.network_policy,
             sbr.external_side_effects,
             oe.gateway_mode,
             oe.budget_status,
             oe.gateway_request_count,
             oe.prompt_tokens,
             oe.completion_tokens,
             oe.total_tokens,
             oe.estimated_cost_usd,
             oe.blocked_reason,
             oe.live_model_verified,
             oe.model_name,
             oe.cli_version,
             rrp.id AS review_package_id,
             rrp.baseline_package_id,
             rrp.package_status,
             rrp.content_snapshot_complete,
             rrp.artifact_manifest,
             rrp.artifact_diff,
             rrp.dependency_inventory,
             rrp.sbom,
             rrp.test_report,
             rrp.risk_summary,
             rrp.source_tree_sha256,
             rrp.package_sha256,
             rrp.generator_version,
             rrp.generated_at AS review_generated_at
      FROM release_candidates rc
      JOIN build_proposals bp ON bp.id=rc.proposal_id
      JOIN digital_asset_opportunities o ON o.id=bp.opportunity_id
      JOIN sandbox_build_requests sbr ON sbr.id=rc.request_id
      LEFT JOIN openhands_executions oe ON oe.request_id=rc.request_id
      LEFT JOIN release_review_packages rrp ON rrp.release_candidate_id=rc.id
      WHERE rc.id=:id
    """)
    def _load_detail():
        with engine.connect() as db:
            row=db.execute(sql,{"id":candidate_id}).mappings().one_or_none()
            if row is None:
                raise LookupError("Release candidate not found")
            decisions=[
                dict(item) for item in db.execute(text("""
                  SELECT id,candidate_status,decision,reason,actor,decided_at,
                         review_package_id,review_package_sha256,source_tree_sha256
                  FROM release_decisions
                  WHERE release_candidate_id=:id
                  ORDER BY decided_at DESC,id DESC
                """),{"id":candidate_id}).mappings().all()
            ]
            return row,decisions

    row,decisions=read_with_retry("review_workspace_detail",_load_detail)
    result=dict(row)
    integrity_gate=evaluate_release_integrity(
        result.get("source_tree_sha256"),
        list(result.get("artifact_manifest") or []),
    )

    def _load_integrity_blocks():
        with engine.begin() as audit_db:
            return list_release_integrity_blocks(audit_db,candidate_id)

    integrity_blocks=read_with_retry(
        "review_workspace_integrity_blocks",
        _load_integrity_blocks,
    )
    terminal=result["release_status"] in {"RELEASE_APPROVED","RELEASE_REJECTED"}
    live_ok=(
        result["live_validation_verified"] is True
        and result["live_model_verified"] is True
        and result["gateway_mode"]=="PROXY"
        and result["budget_status"]=="WITHIN_BUDGET"
        and result["request_status"]=="ARTIFACT_READY"
    )
    review_ok=(
        result["review_package_id"] is not None
        and result["package_status"]=="GENERATED"
        and result["content_snapshot_complete"] is True
        and bool(result["package_sha256"])
        and bool(result["source_tree_sha256"])
    )
    result["release_gate_configured"]=bool(settings.human_release_key.strip())
    result["can_approve"]=(
        not terminal
        and result["release_status"]=="READY_FOR_REVIEW"
        and live_ok
        and review_ok
        and integrity_gate["allowed"] is True
        and result["deployment_enabled"] is False
        and result["execution_enabled"] is False
    )
    result["can_reject"]=not terminal
    result["integrity_gate"]=integrity_gate
    result["integrity_status"]=integrity_gate["integrity_status"]
    result["integrity_gate_allowed"]=integrity_gate["allowed"]
    result["integrity_blocking_reasons"]=integrity_gate.get("blocking_reasons") or []
    result["integrity_block_events"]=integrity_blocks
    result["decisions"]=decisions
    result["ui_safety"]={
        "deployment_enabled":False,
        "auto_deploy":False,
        "release_key_persisted_in_browser":False,
        "approval_requires_live_validation":True,
        "approval_requires_integrity_verified":True,
    }
    return result
