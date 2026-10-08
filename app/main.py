import hashlib
import httpx
import os
import secrets
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
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
from app.provider_composition import (
    list_provider_composition_runs,
    list_provider_compositions,
    run_provider_composition_cycle,
)
from app.production_provider_contract import (
    create_provider_job,
    get_provider_job,
    list_provider_definitions,
    list_provider_jobs,
)
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
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.asset_registry import list_animation_registry
from app.providers.animation.shrimp.provider import get_shrimp_animation_job
from app.providers.animation.shrimp.resource_planning import list_resource_plans
from app.providers.animation.shrimp.execution import list_shrimp_artifacts
from app.providers.animation.shrimp.animation_execution import (
    list_animation_compositions,
)
from app.providers.animation.shrimp.human_review import (
    decide_shrimp_release,
    get_episode_bundle_file,
    get_episode_player_file,
    get_review_document_file,
    get_shrimp_review_workspace,
    list_shrimp_review_workspace,
)
from app.providers.animation.shrimp.publishing_authorization import (
    create_publish_plan,
    decide_publish_authorization,
    get_publish_plan,
    get_publish_target,
    list_publish_plans,
    list_publish_targets,
    register_publish_target,
)
from app.providers.animation.shrimp.publisher_execution import (
    create_publish_execution,
    get_publish_execution,
    get_publish_execution_for_plan,
    list_publish_executions,
    publish_uploaded_media,
    reconcile_publish_upload,
    reconcile_published_media,
    upload_publish_media,
)
from app.providers.animation.shrimp.bilibili_credentials import (
    create_credential_slot,
    get_credential_slot,
    list_credential_slots,
    list_health_checks,
    run_credential_health_check,
    run_health_monitor,
    list_health_monitor_runs,
    rotate_credential_slot,
    select_failover_sacrificial_accounts,
    select_healthy_sacrificial_account,
    set_credential_slot_status,
    set_slot_selection_priority,
)
from app.providers.animation.shrimp.bilibili_incident_ops import (
    acknowledge_incident,
    add_corrective_action,
    assign_incident_owner,
    complete_corrective_action,
    configure_oncall_route,
    bootstrap_incident_operations,
    evaluate_incident_slas,
    incident_ops_summary,
    list_corrective_actions,
    list_oncall_routes,
    list_incident_sla_events,
    list_post_incident_reviews,
    update_pir,
)
from app.providers.animation.shrimp.bilibili_incidents import (
    apply_approved_recovery,
    decide_recovery,
    deliver_notifications,
    incident_timeline,
    list_incidents,
    list_notifications,
    list_recovery_approvals,
    queue_notification,
    request_recovery,
    sync_critical_incidents,
)
from app.providers.animation.shrimp.bilibili_certification_trust_audit import (
    list_audit_proofs,
    list_integrity_audits,
    list_renewal_escalations,
    run_integrity_audit,
    run_trust_audit_cycle,
    trust_audit_dashboard,
)
from app.providers.animation.shrimp.bilibili_external_verification import (
    append_export_registry,
    create_signed_proof_bundle,
    external_verification_dashboard,
    get_proof_bundle,
    list_export_registry,
    list_external_anchors,
    list_proof_bundles,
    register_external_anchor,
    verify_export_registry_chain,
    verify_proof_bundle,
)
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    bootstrap_signing_trust,
    list_signing_key_events,
    list_signing_key_states,
    list_trust_roots,
    revoke_signing_key,
    rotate_signing_key,
    signing_key_lifecycle_dashboard,
    verify_all_bundles_with_key_registry,
    verify_bundle_with_key_registry,
    verify_trust_root_chain,
)
from app.providers.animation.shrimp.bilibili_multisigner_trust import (
    add_transition_signature,
    apply_root_transition,
    create_root_transition_plan,
    decide_transition,
    get_transition_plan,
    list_key_compromise_recovery_drills,
    multisigner_dashboard,
    run_key_compromise_recovery_drill,
    transition_approval_status,
    transition_signature_status,
)
from app.providers.animation.shrimp.bilibili_openbao_live_acceptance import (
    list_openbao_live_acceptances,
    run_openbao_live_acceptance,
)
from app.providers.animation.shrimp.bilibili_transparency_dsse import (
    add_dsse_signature,
    append_to_rekor,
    append_to_rekor_compatible,
    create_dsse_attestation,
    export_offline_bundle,
    request_trusted_timestamp,
    sign_dsse_attestation_current,
    transparency_dashboard,
    verify_dsse_threshold,
    verify_offline,
)
from app.providers.animation.shrimp.bilibili_hsm_root_ceremony import (
    create_root_ceremony,
    generate_offline_root_backup,
    hsm_root_custody_dashboard,
    register_current_hsm_key,
    run_restore_drill,
)
from app.providers.animation.shrimp.bilibili_external_kms_registry import (
    create_cross_kms_root_ceremony,
    external_kms_dashboard,
    sync_configured_provider_registry,
)
from app.providers.animation.shrimp.bilibili_live_cloud_kms_acceptance import (
    live_cloud_kms_dashboard,
    run_live_cross_cloud_acceptance,
    run_live_provider_acceptance,
)
from app.providers.animation.shrimp.bilibili_live_cloud_identity import (
    live_cloud_identity_readiness,
)
from app.providers.animation.shrimp.bilibili_certification_renewal import (
    decide_recertification,
    evaluate_certification_expiry,
    list_attestations,
    list_recertification_candidates,
    list_recertification_decisions,
    renewal_dashboard,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    certification_dashboard,
    evaluate_certification,
    generate_certification,
    get_certification,
    list_certifications,
    list_reopen_evaluations,
    list_reopen_events,
    run_certification_cycle,
)
from app.providers.animation.shrimp.bilibili_post_unfreeze_observation import (
    accept_restore,
    evaluate_observation,
    get_observation_session,
    list_observation_sessions,
    list_ramp_evaluations,
    list_restore_acceptances,
    observation_dashboard,
)
from app.providers.animation.shrimp.bilibili_reliability_restore import (
    first_restore_approval,
    generate_restore_plan,
    get_restore_plan,
    list_restore_approvals,
    list_restore_plans,
    safe_unfreeze_dashboard,
    second_restore_apply,
)
from app.providers.animation.shrimp.bilibili_reliability_policy_change import (
    decide_change_plan,
    generate_change_plan,
    get_change_plan,
    get_policy_control,
    list_apply_decisions,
    list_change_plans,
    list_control_events,
    policy_change_dashboard,
)
from app.providers.animation.shrimp.bilibili_reliability_governance import (
    decide_governance_review,
    generate_governance_review,
    get_governance_review,
    governance_dashboard,
    list_governance_decisions,
    list_governance_reviews,
    list_policy_intents,
)
from app.providers.animation.shrimp.bilibili_reliability_trend import (
    list_burn_rates,
    list_policy_recommendations,
    list_regressions,
    list_trend_points,
    run_reliability_analysis,
    trend_dashboard,
)
from app.providers.animation.shrimp.bilibili_reliability import (
    generate_reliability_scorecards,
    list_recurrence_clusters,
    list_reliability_scorecards,
    reliability_dashboard,
)
from app.providers.animation.shrimp.bilibili_recovery_policy import (
    evaluate_account_circuit,
    list_circuit_breakers,
    list_circuit_events,
    list_claim_escalations,
    list_recovery_policy_runs,
    operations_console,
    run_recovery_policy,
)
from app.providers.animation.shrimp.bilibili_quota_ops import (
    list_daily_quota_audits,
    list_stuck_claims,
    list_stuck_reconciliations,
    quota_dashboard,
    reconcile_stuck_claim,
    run_daily_quota_audit,
)
from app.providers.animation.shrimp.bilibili_quota import (
    get_quota_usage,
    list_execution_claims,
    list_quota_ledger,
)
from app.providers.animation.shrimp.bilibili_router import (
    create_pre_publish_reservation,
    get_reservation,
    list_reservations,
    rebind_pre_publish_reservation,
    release_reservation,
)
from app.providers.animation.shrimp.bilibili_accounts import (
    create_bilibili_account,
    get_bilibili_account,
    list_bilibili_accounts,
    update_bilibili_account,
)
from app.providers.animation.shrimp.bilibili_live_acceptance import (
    get_bilibili_live_acceptance,
    run_bilibili_live_acceptance,
)
from app.providers.animation.shrimp.audit_evidence_explorer import (
    audit_evidence_explorer,
    export_audit_evidence,
)
from app.providers.animation.shrimp.control_center_final_acceptance import (
    evaluate_unified_control_center_acceptance,
)

app = FastAPI(title="AI Digital Asset Factory", version="0.3.0")

@app.on_event("startup")
def _apply_startup_migrations():
    migrate()
    if (
        settings.production_provider_contract_enabled
        and settings.shrimp_animation_provider_enabled
    ):
        register_shrimp_animation_provider()

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

class ProductionProviderJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_key: str = Field(min_length=2,max_length=64)
    proposal_id: UUID
    requested_by: str = Field(default="provider-api",min_length=1,max_length=200)

class ShrimpHumanReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE","REJECT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(
        default="shrimp-human-review-api",
        min_length=1,
        max_length=200,
    )
    episode_bundle_sha256: str = Field(min_length=64,max_length=64)
    release_review_package_sha256: str = Field(
        min_length=64,
        max_length=64,
    )
    confirmed_checklist: list[str] = Field(
        default_factory=list,
        max_length=20,
    )

class ShrimpBilibiliCredentialRotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_env_prefix: str = Field(min_length=3,max_length=121)
    reason: str = Field(min_length=3,max_length=1000)
    actor: str = Field(default="shrimp-credential-rotation",min_length=1,max_length=200)

class ShrimpBilibiliSlotPriorityUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selection_priority: int = Field(ge=1,le=10000)
    actor: str = Field(default="shrimp-slot-priority",min_length=1,max_length=200)

class ShrimpBilibiliCredentialSlotCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_key: str = Field(min_length=3,max_length=120)
    slot_key: str = Field(min_length=3,max_length=120)
    env_prefix: str = Field(min_length=3,max_length=121)
    actor: str = Field(default="shrimp-credential-slot-api",min_length=1,max_length=200)

class ShrimpBilibiliCredentialSlotUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slot_status: Literal["ACTIVE","INACTIVE"]
    actor: str = Field(default="shrimp-credential-slot-api",min_length=1,max_length=200)

class ShrimpBilibiliHealthCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-credential-health",min_length=1,max_length=200)

class ShrimpBilibiliAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_key: str = Field(min_length=3,max_length=120)
    display_name: str = Field(min_length=1,max_length=200)
    mid: str = Field(min_length=1,max_length=32)
    tags: list[str] = Field(default_factory=list,max_length=30)
    default_tid: int = Field(default=122,ge=1)
    default_copyright: Literal["ORIGINAL","REPOST"] = "ORIGINAL"
    default_description: str = Field(default="",max_length=5000)
    default_tags: list[str] = Field(default_factory=list,max_length=30)
    cover_strategy: Literal["REQUIRE_ARTIFACT","OPTIONAL","NONE"] = "REQUIRE_ARTIFACT"
    daily_publish_limit: int = Field(default=1,ge=0,le=100)
    publish_window_start: str | None = None
    publish_window_end: str | None = None
    timezone: str = Field(default="Asia/Shanghai",min_length=1,max_length=100)
    safety_policy: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(default="shrimp-account-api",min_length=1,max_length=200)

class ShrimpBilibiliAccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None,min_length=1,max_length=200)
    account_status: Literal["ACTIVE","INACTIVE"] | None = None
    tags: list[str] | None = Field(default=None,max_length=30)
    default_tid: int | None = Field(default=None,ge=1)
    default_copyright: Literal["ORIGINAL","REPOST"] | None = None
    default_description: str | None = Field(default=None,max_length=5000)
    default_tags: list[str] | None = Field(default=None,max_length=30)
    cover_strategy: Literal["REQUIRE_ARTIFACT","OPTIONAL","NONE"] | None = None
    daily_publish_limit: int | None = Field(default=None,ge=0,le=100)
    publish_window_start: str | None = None
    publish_window_end: str | None = None
    timezone: str | None = Field(default=None,min_length=1,max_length=100)
    safety_policy: dict[str, Any] | None = None
    actor: str = Field(default="shrimp-account-api",min_length=1,max_length=200)

class ShrimpPublishTargetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_key: str = Field(min_length=3,max_length=120)
    platform: Literal["BILIBILI","YOUTUBE","CUSTOM"]
    display_name: str = Field(min_length=1,max_length=200)
    account_reference: str | None = Field(default=None,max_length=300)
    metadata_constraints: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(
        default="shrimp-publish-target-api",
        min_length=1,
        max_length=200,
    )

class ShrimpBilibiliReservationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exclude_account_key: str | None = Field(default=None,max_length=120)
    actor: str = Field(default="shrimp-bilibili-router",min_length=1,max_length=200)

class ShrimpBilibiliReservationRelease(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3,max_length=500)
    actor: str = Field(default="shrimp-bilibili-router",min_length=1,max_length=200)

class ShrimpBilibiliRoutedPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reservation_id: UUID
    publish_metadata: dict[str, Any]
    actor: str = Field(default="shrimp-routed-publish-plan-api",min_length=1,max_length=200)

class ShrimpPublishPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_key: str = Field(min_length=3,max_length=120)
    reservation_id: UUID | None = None
    publish_metadata: dict[str, Any]
    actor: str = Field(
        default="shrimp-publish-plan-api",
        min_length=1,
        max_length=200,
    )

class ShrimpPublishAuthorizationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["AUTHORIZE","REJECT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(
        default="shrimp-publish-auth-api",
        min_length=1,
        max_length=200,
    )
    plan_sha256: str = Field(min_length=64,max_length=64)
    dry_run_sha256: str = Field(min_length=64,max_length=64)

class ShrimpBilibiliOncallRouteUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Literal["WARNING","CRITICAL"]
    owner_ref: str = Field(min_length=1,max_length=200)
    secondary_owner_ref: str | None = Field(default=None,max_length=200)
    actor: str = Field(default="shrimp-incident-ops",min_length=1,max_length=200)

class ShrimpBilibiliCorrectiveActionComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    completion_evidence: str = Field(min_length=3,max_length=4000)
    actor: str = Field(default="shrimp-pir-reviewer",min_length=1,max_length=200)

class ShrimpBilibiliIncidentOwnerAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_ref: str = Field(min_length=1,max_length=200)
    actor: str = Field(default="shrimp-incident-ops",min_length=1,max_length=200)

class ShrimpBilibiliPirComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root_cause: str = Field(min_length=3,max_length=4000)
    lessons_learned: str = Field(min_length=3,max_length=4000)
    actor: str = Field(default="shrimp-pir-reviewer",min_length=1,max_length=200)

class ShrimpBilibiliCorrectiveActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=3,max_length=4000)
    owner_ref: str = Field(min_length=1,max_length=200)
    due_at: str | None = None
    actor: str = Field(default="shrimp-pir-reviewer",min_length=1,max_length=200)

class ShrimpBilibiliRecertificationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE","REJECT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(
        default="shrimp-recertification-governance",
        min_length=1,
        max_length=200,
    )
    candidate_sha256: str = Field(min_length=64,max_length=64)

class ShrimpBilibiliRestoreAcceptanceAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(
        default="shrimp-restore-acceptance",
        min_length=1,
        max_length=200,
    )

class ShrimpBilibiliReliabilityRestorePlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(
        default="shrimp-reliability-restore-planner",
        min_length=1,
        max_length=200,
    )

class ShrimpBilibiliReliabilityRestoreDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE","REJECT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(min_length=1,max_length=200)
    plan_sha256: str = Field(min_length=64,max_length=64)
    dry_run_sha256: str = Field(min_length=64,max_length=64)

class ShrimpBilibiliReliabilityChangePlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(
        default="shrimp-reliability-change-planner",
        min_length=1,
        max_length=200,
    )

class ShrimpBilibiliReliabilityChangeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPLY","REJECT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(
        default="shrimp-reliability-change-approver",
        min_length=1,
        max_length=200,
    )
    plan_sha256: str = Field(min_length=64,max_length=64)
    dry_run_sha256: str = Field(min_length=64,max_length=64)

class ShrimpBilibiliAuditBundleAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(
        default="shrimp-audit-proof-bundle",
        min_length=1,
        max_length=200,
    )

class ShrimpBilibiliSigningTrustBootstrap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-signing-trust-bootstrap",min_length=1,max_length=200)
    key_label: str = Field(default="primary",min_length=1,max_length=200)


class ShrimpBilibiliSigningKeyRotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-signing-key-rotation",min_length=1,max_length=200)
    reason: str = Field(min_length=3,max_length=1000)
    key_label: str = Field(default="rotated",min_length=1,max_length=200)


class ShrimpBilibiliSigningKeyRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-signing-key-revocation",min_length=1,max_length=200)
    reason: str = Field(min_length=3,max_length=1000)
    effective_at: datetime


class ShrimpBilibiliRootTransitionPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_fingerprints: list[str] = Field(min_length=1,max_length=10)
    candidate_threshold: int = Field(ge=1,le=10)
    transition_type: Literal["ROTATION","COMPROMISE_RECOVERY","POLICY_UPDATE"]
    actor: str = Field(default="shrimp-root-transition-planner",min_length=1,max_length=200)


class ShrimpBilibiliRootTransitionSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(min_length=64,max_length=64)
    signature_b64: str = Field(min_length=16,max_length=4096)
    actor: str = Field(default="shrimp-root-transition-signer",min_length=1,max_length=200)


class ShrimpBilibiliRootTransitionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE","REJECT"]
    reason: str = Field(min_length=3,max_length=1000)


class ShrimpBilibiliRootTransitionApply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-root-transition-apply",min_length=1,max_length=200)


class ShrimpBilibiliCompromiseRecoveryDrill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compromised_fingerprint: str = Field(min_length=64,max_length=64)
    affected_bundle_ids: list[UUID] = Field(min_length=1,max_length=100)
    actor: str = Field(default="shrimp-compromise-recovery-drill",min_length=1,max_length=200)


class ShrimpBilibiliOpenBaoLiveAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-openbao-live-acceptance",min_length=1,max_length=200)


class ShrimpBilibiliDsseAttestationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_type: str = Field(min_length=1,max_length=100)
    subject_id: str = Field(min_length=1,max_length=500)
    subject_sha256: str = Field(min_length=64,max_length=64)
    predicate: dict[str,Any] = Field(default_factory=dict)
    actor: str = Field(default="shrimp-dsse-attestation",min_length=1,max_length=200)


class ShrimpBilibiliDsseSignatureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fingerprint: str = Field(min_length=64,max_length=64)
    signature_b64: str = Field(min_length=16,max_length=4096)
    public_key_pem_b64: str = Field(min_length=16,max_length=16000)
    actor: str = Field(default="shrimp-dsse-signer",min_length=1,max_length=200)


class ShrimpBilibiliAttestationAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-attestation-action",min_length=1,max_length=200)


class ShrimpBilibiliRootCeremonyAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ceremony_type: Literal["BOOTSTRAP","ROTATION","DISASTER_RECOVERY","RESTORE_VALIDATION"] = "RESTORE_VALIDATION"
    actor: str = Field(default="shrimp-root-ceremony",min_length=1,max_length=200)


class ShrimpBilibiliRootBackupAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-root-backup",min_length=1,max_length=200)


class ShrimpBilibiliRootRestoreDrillAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-root-restore-drill",min_length=1,max_length=200)


class ShrimpBilibiliExternalKmsSyncAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-external-kms-sync",min_length=1,max_length=200)


class ShrimpBilibiliCrossKmsCeremonyAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_refs: list[str] = Field(min_length=2,max_length=10)
    threshold: int = Field(default=2,ge=2,le=10)
    actor: str = Field(default="shrimp-cross-kms-ceremony",min_length=1,max_length=200)


class ShrimpBilibiliLiveCloudKmsAcceptanceAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_type: Literal["AWS_KMS","GCP_KMS","AZURE_KEY_VAULT"]
    actor: str = Field(default="shrimp-live-cloud-kms",min_length=1,max_length=200)


class ShrimpBilibiliLiveCrossCloudAcceptanceAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_types: list[Literal["AWS_KMS","GCP_KMS","AZURE_KEY_VAULT"]] = Field(min_length=2,max_length=3)
    threshold: int = Field(default=2,ge=2,le=3)
    actor: str = Field(default="shrimp-live-cross-cloud",min_length=1,max_length=200)


class ShrimpBilibiliExternalAnchorCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anchor_provider: str = Field(min_length=2,max_length=120)
    anchor_reference: str = Field(min_length=2,max_length=500)
    anchor_digest_sha256: str = Field(min_length=64,max_length=64)
    receipt: dict[str, Any] = Field(default_factory=dict)
    actor: str = Field(
        default="shrimp-external-anchor",
        min_length=1,
        max_length=200,
    )

class ShrimpBilibiliExportRegistryAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(
        default="shrimp-audit-export",
        min_length=1,
        max_length=200,
    )

class ShrimpBilibiliGovernanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal[
        "ACCEPT_NORMAL",
        "ACCEPT_CAUTION",
        "AUTHORIZE_FREEZE_INTENT",
        "REJECT_RECOMMENDATION",
    ]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(
        default="shrimp-reliability-governance",
        min_length=1,
        max_length=200,
    )

class ShrimpBilibiliIncidentAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(default="shrimp-incident-api",min_length=1,max_length=200)

class ShrimpBilibiliRecoveryDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE","REJECT"]
    reason: str = Field(min_length=3,max_length=4000)
    actor: str = Field(default="shrimp-recovery-approver",min_length=1,max_length=200)

class ShrimpBilibiliCircuitEvaluateAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(
        default="shrimp-circuit-evaluate",
        min_length=1,
        max_length=200,
    )

class ShrimpBilibiliStuckReconcileAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(
        default="shrimp-quota-ops-reconcile",
        min_length=1,
        max_length=200,
    )

class ShrimpPublishExecutionAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actor: str = Field(
        default="shrimp-publish-execution-api",
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

def _require_shrimp_review_key(provided: str | None) -> None:
    expected=settings.shrimp_human_review_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Shrimp human review gate is not configured",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Shrimp human review key",
        )

def _shrimp_publish_key_independent() -> bool:
    expected=settings.shrimp_publish_authorization_key.strip()
    if not expected:
        return False
    earlier_keys=(
        settings.human_approval_key.strip(),
        settings.human_release_key.strip(),
        settings.human_deployment_key.strip(),
        settings.human_production_execution_key.strip(),
        settings.shrimp_human_review_key.strip(),
    )
    return all(
        not value or not secrets.compare_digest(expected,value)
        for value in earlier_keys
    )

def _require_shrimp_publish_key(provided: str | None) -> None:
    expected=settings.shrimp_publish_authorization_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Shrimp publish authorization gate is not configured",
        )
    if not _shrimp_publish_key_independent():
        raise HTTPException(
            status_code=503,
            detail="Shrimp publish authorization key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Shrimp publish authorization key",
        )

def _shrimp_publish_execution_key_independent() -> bool:
    expected=settings.shrimp_publish_execution_key.strip()
    if not expected:
        return False
    earlier_keys=(
        settings.human_approval_key.strip(),
        settings.human_release_key.strip(),
        settings.human_deployment_key.strip(),
        settings.human_production_execution_key.strip(),
        settings.shrimp_human_review_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
    )
    return all(
        not value or not secrets.compare_digest(expected,value)
        for value in earlier_keys
    )

def _require_shrimp_publish_execution_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_publish_execution_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Shrimp publish execution gate is not configured",
        )
    if not _shrimp_publish_execution_key_independent():
        raise HTTPException(
            status_code=503,
            detail="Shrimp publish execution key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Shrimp publish execution key",
        )

def _shrimp_bilibili_live_acceptance_key_independent() -> bool:
    expected=settings.shrimp_bilibili_live_acceptance_key.strip()
    if not expected:
        return False
    earlier_keys=(
        settings.human_approval_key.strip(),
        settings.human_release_key.strip(),
        settings.human_deployment_key.strip(),
        settings.human_production_execution_key.strip(),
        settings.preview_acceptance_key.strip(),
        settings.shrimp_human_review_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
    )
    return all(
        not value or not secrets.compare_digest(expected,value)
        for value in earlier_keys
    )

def _require_shrimp_bilibili_live_acceptance_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_live_acceptance_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Shrimp Bilibili live acceptance gate is not configured",
        )
    if not _shrimp_bilibili_live_acceptance_key_independent():
        raise HTTPException(
            status_code=503,
            detail="Shrimp Bilibili live acceptance key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Shrimp Bilibili live acceptance key",
        )

def _require_shrimp_bilibili_recertification_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_recertification_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Bilibili re-certification governance gate is not configured",
        )
    forbidden=(
        settings.shrimp_bilibili_restore_acceptance_key.strip(),
        settings.shrimp_bilibili_reliability_restore_approval_key.strip(),
        settings.shrimp_bilibili_reliability_restore_apply_key.strip(),
        settings.shrimp_bilibili_reliability_policy_apply_key.strip(),
        settings.shrimp_bilibili_reliability_governance_key.strip(),
        settings.shrimp_bilibili_health_monitor_key.strip(),
        settings.shrimp_bilibili_incident_ops_key.strip(),
        settings.shrimp_bilibili_recovery_approval_key.strip(),
        settings.shrimp_bilibili_live_acceptance_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
        settings.shrimp_human_review_key.strip(),
    )
    if any(
        value and secrets.compare_digest(expected,value)
        for value in forbidden
    ):
        raise HTTPException(
            status_code=503,
            detail="Bilibili re-certification key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili re-certification key",
        )


def _require_shrimp_bilibili_restore_acceptance_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_restore_acceptance_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Bilibili restore acceptance gate is not configured",
        )
    forbidden=(
        settings.shrimp_bilibili_reliability_restore_approval_key.strip(),
        settings.shrimp_bilibili_reliability_restore_apply_key.strip(),
        settings.shrimp_bilibili_reliability_policy_apply_key.strip(),
        settings.shrimp_bilibili_reliability_governance_key.strip(),
        settings.shrimp_bilibili_health_monitor_key.strip(),
        settings.shrimp_bilibili_incident_ops_key.strip(),
        settings.shrimp_bilibili_recovery_approval_key.strip(),
        settings.shrimp_bilibili_live_acceptance_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
        settings.shrimp_human_review_key.strip(),
    )
    if any(
        value and secrets.compare_digest(expected,value)
        for value in forbidden
    ):
        raise HTTPException(
            status_code=503,
            detail="Bilibili restore acceptance key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili restore acceptance key",
        )


def _require_shrimp_bilibili_reliability_restore_approval_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_reliability_restore_approval_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Bilibili reliability restore approval gate is not configured",
        )
    forbidden=(
        settings.shrimp_bilibili_reliability_restore_apply_key.strip(),
        settings.shrimp_bilibili_reliability_policy_apply_key.strip(),
        settings.shrimp_bilibili_reliability_governance_key.strip(),
        settings.shrimp_bilibili_incident_ops_key.strip(),
        settings.shrimp_bilibili_recovery_approval_key.strip(),
        settings.shrimp_bilibili_live_acceptance_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
        settings.shrimp_human_review_key.strip(),
    )
    if any(
        value and secrets.compare_digest(expected,value)
        for value in forbidden
    ):
        raise HTTPException(
            status_code=503,
            detail="Bilibili reliability restore approval key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili reliability restore approval key",
        )


def _require_shrimp_bilibili_reliability_restore_apply_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_reliability_restore_apply_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Bilibili reliability restore apply gate is not configured",
        )
    forbidden=(
        settings.shrimp_bilibili_reliability_restore_approval_key.strip(),
        settings.shrimp_bilibili_reliability_policy_apply_key.strip(),
        settings.shrimp_bilibili_reliability_governance_key.strip(),
        settings.shrimp_bilibili_incident_ops_key.strip(),
        settings.shrimp_bilibili_recovery_approval_key.strip(),
        settings.shrimp_bilibili_live_acceptance_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
        settings.shrimp_human_review_key.strip(),
    )
    if any(
        value and secrets.compare_digest(expected,value)
        for value in forbidden
    ):
        raise HTTPException(
            status_code=503,
            detail="Bilibili reliability restore apply key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili reliability restore apply key",
        )


def _require_shrimp_bilibili_reliability_policy_apply_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_reliability_policy_apply_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Bilibili reliability policy apply gate is not configured",
        )
    forbidden=(
        settings.shrimp_bilibili_reliability_governance_key.strip(),
        settings.shrimp_bilibili_incident_ops_key.strip(),
        settings.shrimp_bilibili_recovery_approval_key.strip(),
        settings.shrimp_bilibili_live_acceptance_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
        settings.shrimp_human_review_key.strip(),
    )
    if any(
        value and secrets.compare_digest(expected,value)
        for value in forbidden
    ):
        raise HTTPException(
            status_code=503,
            detail="Bilibili reliability policy apply key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili reliability policy apply key",
        )


def _require_shrimp_bilibili_reliability_governance_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_reliability_governance_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Bilibili reliability governance gate is not configured",
        )
    forbidden=(
        settings.shrimp_bilibili_incident_ops_key.strip(),
        settings.shrimp_bilibili_recovery_approval_key.strip(),
        settings.shrimp_bilibili_live_acceptance_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
        settings.shrimp_human_review_key.strip(),
    )
    if any(
        value and secrets.compare_digest(expected,value)
        for value in forbidden
    ):
        raise HTTPException(
            status_code=503,
            detail="Bilibili reliability governance key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili reliability governance key",
        )



def _require_shrimp_bilibili_signing_key_rotation_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_signing_key_rotation_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Bilibili signing key rotation gate is not configured",
        )
    forbidden=(
        settings.shrimp_bilibili_reliability_governance_key.strip(),
        settings.shrimp_bilibili_reliability_policy_apply_key.strip(),
        settings.shrimp_bilibili_reliability_restore_apply_key.strip(),
        settings.shrimp_bilibili_live_acceptance_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
        settings.shrimp_human_review_key.strip(),
    )
    if any(
        value and secrets.compare_digest(expected,value)
        for value in forbidden
    ):
        raise HTTPException(
            status_code=503,
            detail="Bilibili signing key rotation key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili signing key rotation key",
        )


def _require_shrimp_bilibili_root_transition_approver(
    slot: str,
    provided: str | None,
) -> str:
    a=settings.shrimp_bilibili_root_transition_approver_a_key.strip()
    b=settings.shrimp_bilibili_root_transition_approver_b_key.strip()
    rotation=settings.shrimp_bilibili_signing_key_rotation_key.strip()
    if not a or not b:
        raise HTTPException(
            status_code=503,
            detail="Root transition dual-control gates are not configured",
        )
    if secrets.compare_digest(a,b):
        raise HTTPException(
            status_code=503,
            detail="Root transition approver keys must be independent",
        )
    for value in (a,b):
        if rotation and secrets.compare_digest(value,rotation):
            raise HTTPException(
                status_code=503,
                detail="Root transition approver keys must be independent from rotation key",
            )
    expected=a if slot=="A" else b
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(
            status_code=403,
            detail=f"Invalid root transition approver {slot} key",
        )
    return f"root-transition-approver-{slot.lower()}"


def _require_shrimp_bilibili_root_ceremony_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_root_ceremony_key.strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Root ceremony gate is not configured",
        )
    forbidden=(
        settings.shrimp_bilibili_signing_key_rotation_key.strip(),
        settings.shrimp_bilibili_root_transition_approver_a_key.strip(),
        settings.shrimp_bilibili_root_transition_approver_b_key.strip(),
        settings.shrimp_bilibili_reliability_governance_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
    )
    if any(
        value and secrets.compare_digest(expected,value)
        for value in forbidden
    ):
        raise HTTPException(
            status_code=503,
            detail="Root ceremony key must be independent",
        )
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(status_code=403,detail="Invalid root ceremony key")

def _require_shrimp_bilibili_incident_ops_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_incident_ops_key.strip()
    if not expected:
        raise HTTPException(status_code=503,detail="Bilibili incident ops gate is not configured")
    forbidden=(
        settings.shrimp_bilibili_recovery_approval_key.strip(),
        settings.shrimp_bilibili_live_acceptance_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
    )
    if any(value and secrets.compare_digest(expected,value) for value in forbidden):
        raise HTTPException(status_code=503,detail="Bilibili incident ops key must be independent")
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(status_code=403,detail="Invalid Bilibili incident ops key")


def _require_shrimp_bilibili_recovery_approval_key(
    provided: str | None,
) -> None:
    expected=settings.shrimp_bilibili_recovery_approval_key.strip()
    if not expected:
        raise HTTPException(status_code=503,detail="Bilibili recovery approval gate is not configured")
    forbidden=(
        settings.shrimp_human_review_key.strip(),
        settings.shrimp_publish_authorization_key.strip(),
        settings.shrimp_publish_execution_key.strip(),
        settings.shrimp_bilibili_live_acceptance_key.strip(),
    )
    if any(value and secrets.compare_digest(expected,value) for value in forbidden):
        raise HTTPException(status_code=503,detail="Bilibili recovery approval key must be independent")
    if provided is None or not secrets.compare_digest(provided,expected):
        raise HTTPException(status_code=403,detail="Invalid Bilibili recovery approval key")


def _shrimp_bilibili_live_acceptance_readiness() -> dict[str, Any]:
    vercel_env=(os.getenv("VERCEL_ENV") or "").strip().lower()
    try:
        db_selection=database_selection()
        preview_isolated=bool(db_selection.get("preview_isolated"))
        database_source=str(db_selection.get("source") or "")
    except RuntimeError:
        preview_isolated=False
        database_source="UNAVAILABLE"

    publish_auth_gate=(
        bool(settings.shrimp_publish_authorization_key.strip())
        and _shrimp_publish_key_independent()
    )
    publish_execution_gate=(
        bool(settings.shrimp_publish_execution_key.strip())
        and _shrimp_publish_execution_key_independent()
    )
    live_gate=(
        bool(settings.shrimp_bilibili_live_acceptance_key.strip())
        and _shrimp_bilibili_live_acceptance_key_independent()
    )
    cookies_present=all((
        settings.shrimp_bilibili_sessdata.strip(),
        settings.shrimp_bilibili_bili_jct.strip(),
        settings.shrimp_bilibili_dede_user_id.strip(),
    ))
    adapter=(
        settings.shrimp_publish_execution_adapter.strip().upper()
        or "MOCK"
    )
    allowed_accounts=(
        settings.shrimp_publish_execution_allowed_account_ref_list
    )
    denied_accounts=(
        settings.shrimp_publish_execution_denied_account_ref_list
    )
    allowed_targets=(
        settings.shrimp_publish_execution_allowed_target_key_list
    )
    denied_targets=(
        settings.shrimp_publish_execution_denied_target_key_list
    )

    counts={
        "release_approved_episodes":0,
        "active_bilibili_targets":0,
        "authorized_bilibili_plans":0,
        "bilibili_controlled_executions":0,
        "runnable_bilibili_executions":0,
        "bilibili_credential_slots":0,
        "healthy_bilibili_credential_slots":0,
    }
    try:
        with engine.connect() as db:
            counts["release_approved_episodes"]=db.execute(text("""
              SELECT COUNT(*)
              FROM shrimp_animation_jobs
              WHERE review_status='RELEASE_APPROVED'
            """)).scalar_one()
            counts["active_bilibili_targets"]=db.execute(text("""
              SELECT COUNT(*)
              FROM shrimp_animation_publish_targets
              WHERE platform='BILIBILI'
                AND target_status='ACTIVE'
                AND account_reference IS NOT NULL
            """)).scalar_one()
            counts["authorized_bilibili_plans"]=db.execute(text("""
              SELECT COUNT(*)
              FROM shrimp_animation_publish_plans
              WHERE platform='BILIBILI'
                AND plan_status='PUBLISH_AUTHORIZED'
            """)).scalar_one()
            counts["bilibili_controlled_executions"]=db.execute(text("""
              SELECT COUNT(*)
              FROM shrimp_animation_publish_executions
              WHERE platform='BILIBILI'
                AND execution_adapter='BILIBILI_CONTROLLED'
            """)).scalar_one()
            counts["runnable_bilibili_executions"]=db.execute(text("""
              SELECT COUNT(*)
              FROM shrimp_animation_publish_executions
              WHERE platform='BILIBILI'
                AND execution_adapter='BILIBILI_CONTROLLED'
                AND source_stale=false
                AND execution_status IN (
                  'SNAPSHOT_CREATED','UPLOAD_UNKNOWN','UPLOADED',
                  'PUBLISH_UNKNOWN','PUBLISHED'
                )
            """)).scalar_one()
            counts["bilibili_credential_slots"]=db.execute(text("""
              SELECT COUNT(*)
              FROM shrimp_bilibili_credential_slots
              WHERE slot_status='ACTIVE'
            """)).scalar_one()
            counts["healthy_bilibili_credential_slots"]=db.execute(text("""
              SELECT COUNT(*)
              FROM shrimp_bilibili_credential_slots
              WHERE slot_status='ACTIVE'
                AND health_status='HEALTHY'
                AND credential_status='CONFIGURED'
                AND login_status='LOGGED_IN'
                AND mid_status='MATCH'
                AND publish_permission_status='ALLOWED'
            """)).scalar_one()
    except Exception:
        counts={key:None for key in counts}

    checks={
        "vercel_preview":vercel_env=="preview",
        "preview_database_isolated":preview_isolated,
        "publish_authorization_gate":publish_auth_gate,
        "publish_execution_gate":publish_execution_gate,
        "publish_executor_enabled":settings.shrimp_publish_executor_enabled,
        "bilibili_controlled_adapter":adapter=="BILIBILI_CONTROLLED",
        "bilibili_live_acceptance_gate":live_gate,
        "bilibili_live_acceptance_enabled":
            settings.shrimp_bilibili_live_acceptance_enabled,
        "bilibili_cookie_credentials_present":
            cookies_present
            or bool(counts["healthy_bilibili_credential_slots"]),
        "sacrificial_account_allowlist_present":bool(allowed_accounts),
        "real_account_denylist_present":bool(denied_accounts),
        "sacrificial_target_allowlist_present":bool(allowed_targets),
        "real_target_denylist_present":bool(denied_targets),
        "allowlist_denylist_disjoint":
            not bool(set(allowed_accounts)&set(denied_accounts))
            and not bool(set(allowed_targets)&set(denied_targets)),
        "runnable_bilibili_execution_present":
            bool(counts["runnable_bilibili_executions"]),
    }
    blockers=[key for key,value in checks.items() if not value]
    return {
        "status":"READY" if not blockers else "BLOCKED",
        "vercel_env":vercel_env or "non-vercel",
        "database_source":database_source,
        "checks":checks,
        "counts":counts,
        "blockers":blockers,
        "secrets_redacted":True,
    }


def _summary_counts(items: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value=str(item.get(key) or "UNKNOWN")
        counts[value]=counts.get(value,0)+1
    return counts


def _control_center_row(row: Any) -> dict[str, Any]:
    result=dict(row)
    for key in (
        "id","provider_job_id","publish_plan_id","target_id",
        "authorization_decision_id","execution_id",
    ):
        if key in result and result[key] is not None:
            result[key]=str(result[key])
    return result


def _shrimp_control_center_summary() -> dict[str, Any]:
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

    jobs=list_provider_jobs(
        limit=200,
        provider_key="shrimp_animation",
    ) if db_state["available"] else []
    job_items=[]
    for row in jobs:
        item=dict(row)
        item["id"]=str(item["id"])
        if item.get("build_proposal_id") is not None:
            item["build_proposal_id"]=str(item["build_proposal_id"])
        job_items.append(item)

    reviews=(
        list_shrimp_review_workspace(limit=200)
        if db_state["available"]
        else []
    )
    targets=(
        list_publish_targets(active_only=False)
        if db_state["available"]
        else []
    )

    plans: list[dict[str, Any]]=[]
    executions: list[dict[str, Any]]=[]
    acceptances: list[dict[str, Any]]=[]
    if db_state["available"]:
        with engine.connect() as db:
            plan_rows=db.execute(text("""
              SELECT pp.id,pp.provider_job_id,pp.platform,pp.target_key,
                     pp.plan_status,pp.dry_run_status,pp.execution_enabled,
                     pp.publish_performed,pp.created_at,pp.authorized_at,
                     pp.rejected_at,pt.display_name AS target_display_name
              FROM shrimp_animation_publish_plans pp
              JOIN shrimp_animation_publish_targets pt ON pt.id=pp.target_id
              ORDER BY pp.created_at DESC,pp.id DESC
              LIMIT 200
            """)).mappings().all()
            plans=[_control_center_row(row) for row in plan_rows]

            execution_rows=db.execute(text("""
              SELECT id,publish_plan_id,provider_job_id,platform,target_key,
                     account_reference,execution_adapter,execution_status,
                     upload_outcome,upload_write_count,publish_outcome,
                     publish_write_count,external_publish_performed,
                     source_stale,created_at,updated_at
              FROM shrimp_animation_publish_executions
              ORDER BY created_at DESC,id DESC
              LIMIT 200
            """)).mappings().all()
            executions=[
                _control_center_row(row)
                for row in execution_rows
            ]

            acceptance_rows=db.execute(text("""
              SELECT id,execution_id,acceptance_status,expected_mid,
                     actual_mid,aid,bvid,archive_state,is_only_self,
                     provider_read_back_verified,
                     private_visibility_verified,cleanup_write_count,
                     cleanup_outcome,cleanup_verified,cleanup_performed,
                     production_account_touched,
                     public_visibility_observed,created_at,updated_at,
                     finished_at
              FROM shrimp_animation_bilibili_live_acceptance_runs
              ORDER BY created_at DESC,id DESC
              LIMIT 100
            """)).mappings().all()
            acceptances=[
                _control_center_row(row)
                for row in acceptance_rows
            ]

    review_counts=_summary_counts(reviews,"review_status")
    plan_counts=_summary_counts(plans,"plan_status")
    execution_counts=_summary_counts(executions,"execution_status")
    acceptance_counts=_summary_counts(
        acceptances,
        "acceptance_status",
    )
    stage_counts=_summary_counts(job_items,"current_stage")
    job_status_counts=_summary_counts(job_items,"job_status")

    readiness=_shrimp_bilibili_live_acceptance_readiness()

    return {
        "status":("READY" if db_state["available"] and migrations.get("status")=="CURRENT" else "DEGRADED"),
        "mode":"READ_ONLY_CONTROL_CENTER",
        "secrets_redacted":True,
        "system":{
            "vercel_env":(
                (os.getenv("VERCEL_ENV") or "").strip().lower()
                or "non-vercel"
            ),
            "database_available":bool(db_state["available"]),
            "database_source":(
                db_state.get("configuration",{}).get("database_source")
            ),
            "preview_isolated":bool(
                db_state.get("configuration",{}).get("preview_isolated")
            ),
            "migration_status":migrations.get("status"),
            "migration_latest":migrations.get("latest_version"),
            "migration_expected_count":migrations.get("expected_count"),
            "migration_applied_count":migrations.get("applied_count"),
            "migration_recorded_count":migrations.get("recorded_count"),
            "migration_unexpected":migrations.get("unexpected",[]),
            "migration_reconciled_legacy":migrations.get("reconciled_legacy",[]),
            "provider":"shrimp_animation",
            "publisher_adapter":(
                settings.shrimp_publish_execution_adapter.strip().upper()
                or "MOCK"
            ),
        },
        "pipeline":{
            "total_jobs":len(job_items),
            "stage_counts":stage_counts,
            "job_status_counts":job_status_counts,
            "recent_jobs":job_items[:12],
        },
        "review":{
            "total":len(reviews),
            "status_counts":review_counts,
            "release_approved":review_counts.get("RELEASE_APPROVED",0),
            "recent_episodes":reviews[:12],
        },
        "publishing":{
            "target_count":len(targets),
            "targets":targets,
            "plan_count":len(plans),
            "plan_status_counts":plan_counts,
            "execution_count":len(executions),
            "execution_status_counts":execution_counts,
            "recent_plans":plans[:10],
            "recent_executions":executions[:10],
        },
        "bilibili":{
            "readiness":readiness,
            "acceptance_count":len(acceptances),
            "acceptance_status_counts":acceptance_counts,
            "recent_acceptances":acceptances[:10],
        },
        "navigation":[
            {"label":"Control Center","href":"/"},
            {"label":"Animation Review","href":"/animation-review"},
            {"label":"Publishing Authorization","href":"/animation-publishing"},
            {"label":"Software Review","href":"/review"},
            {"label":"API Docs","href":"/docs"},
        ],
    }


@app.get("/v1/shrimp-animation/control-center/summary")
def shrimp_animation_control_center_summary():
    try:
        return _shrimp_control_center_summary()
    except DBAPIError as exc:
        if is_database_unavailable(exc):
            raise HTTPException(
                status_code=503,
                detail="Control Center database is unavailable",
            ) from exc
        raise


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

@app.post(
    "/internal/shrimp-animation/bilibili-recovery-policy",
    include_in_schema=False,
)
def shrimp_animation_bilibili_recovery_policy(
    authorization: str | None = Header(default=None,alias="Authorization"),
    x_shrimp_health_monitor_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Health-Monitor-Key",
    ),
):
    provided=(x_shrimp_health_monitor_key or "").strip()
    bearer=(authorization or "").strip()
    expected=settings.shrimp_bilibili_health_monitor_key.strip()
    cron=settings.cron_secret.strip()
    manual_ok=bool(expected) and secrets.compare_digest(provided,expected)
    cron_ok=bool(cron) and bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:].strip(),cron
    )
    if not (manual_ok or cron_ok):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili recovery policy authorization",
        )
    policy=run_recovery_policy(
        actor="scheduled-bilibili-recovery-policy",
        auto_readback=True,
    )
    incidents=sync_critical_incidents(
        actor="scheduled-bilibili-incident-sync"
    )
    for incident in incidents:
        queue_notification(
            incident_id=incident["id"],
            notification_type="INCIDENT_OPENED",
            severity="CRITICAL",
            payload={
                "incident_id":incident["id"],
                "incident_key":incident["incident_key"],
                "operations_path":"/animation/operations",
            },
        )
    incident_ops=bootstrap_incident_operations(
        actor="scheduled-bilibili-incident-ops"
    )
    sla=evaluate_incident_slas(
        actor="scheduled-bilibili-sla"
    )
    notifications=deliver_notifications(
        actor="scheduled-bilibili-notifications"
    )
    return {
        "policy":policy,
        "incidents":incidents,
        "incident_ops":incident_ops,
        "sla":sla,
        "notifications":notifications,
    }


@app.post(
    "/internal/shrimp-animation/bilibili-reliability-governance-review",
    include_in_schema=False,
)
def shrimp_animation_bilibili_reliability_governance_review_generate(
    authorization: str | None = Header(default=None,alias="Authorization"),
    x_shrimp_health_monitor_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Health-Monitor-Key",
    ),
):
    provided=(x_shrimp_health_monitor_key or "").strip()
    bearer=(authorization or "").strip()
    expected=settings.shrimp_bilibili_health_monitor_key.strip()
    cron=settings.cron_secret.strip()
    manual_ok=bool(expected) and secrets.compare_digest(provided,expected)
    cron_ok=bool(cron) and bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:].strip(),cron
    )
    if not (manual_ok or cron_ok):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili governance review authorization",
        )
    try:
        return {
            "status":"REVIEW_GENERATED",
            "review":generate_governance_review(
                actor="scheduled-bilibili-reliability-governance"
            ),
        }
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/internal/shrimp-animation/bilibili-certification-trust-audit-cycle",
    include_in_schema=False,
)
def shrimp_animation_bilibili_certification_trust_audit_cycle(
    authorization: str | None = Header(default=None,alias="Authorization"),
    x_shrimp_health_monitor_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Health-Monitor-Key",
    ),
):
    provided=(x_shrimp_health_monitor_key or "").strip()
    bearer=(authorization or "").strip()
    expected=settings.shrimp_bilibili_health_monitor_key.strip()
    cron=settings.cron_secret.strip()
    manual_ok=bool(expected) and secrets.compare_digest(provided,expected)
    cron_ok=bool(cron) and bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:].strip(),cron
    )
    if not (manual_ok or cron_ok):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili certification trust audit authorization",
        )
    return {
        "status":"AUDITED",
        "result":run_trust_audit_cycle(
            actor="scheduled-bilibili-certification-trust-audit"
        ),
    }


@app.post(
    "/internal/shrimp-animation/bilibili-certification-renewal-cycle",
    include_in_schema=False,
)
def shrimp_animation_bilibili_certification_renewal_cycle(
    authorization: str | None = Header(default=None,alias="Authorization"),
    x_shrimp_health_monitor_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Health-Monitor-Key",
    ),
):
    provided=(x_shrimp_health_monitor_key or "").strip()
    bearer=(authorization or "").strip()
    expected=settings.shrimp_bilibili_health_monitor_key.strip()
    cron=settings.cron_secret.strip()
    manual_ok=bool(expected) and secrets.compare_digest(provided,expected)
    cron_ok=bool(cron) and bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:].strip(),cron
    )
    if not (manual_ok or cron_ok):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili certification renewal authorization",
        )
    return {
        "status":"EVALUATED",
        "result":evaluate_certification_expiry(
            actor="scheduled-bilibili-certification-renewal"
        ),
    }


@app.post(
    "/internal/shrimp-animation/bilibili-post-restore-certification-cycle",
    include_in_schema=False,
)
def shrimp_animation_bilibili_post_restore_certification_cycle(
    authorization: str | None = Header(default=None,alias="Authorization"),
    x_shrimp_health_monitor_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Health-Monitor-Key",
    ),
):
    provided=(x_shrimp_health_monitor_key or "").strip()
    bearer=(authorization or "").strip()
    expected=settings.shrimp_bilibili_health_monitor_key.strip()
    cron=settings.cron_secret.strip()
    manual_ok=bool(expected) and secrets.compare_digest(provided,expected)
    cron_ok=bool(cron) and bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:].strip(),cron
    )
    if not (manual_ok or cron_ok):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili certification cycle authorization",
        )
    try:
        return {
            "status":"EVALUATED",
            "result":run_certification_cycle(
                actor="scheduled-bilibili-post-restore-certification"
            ),
        }
    except RuntimeError as exc:
        if "No active post-restore certification" in str(exc):
            return {"status":"NO_ACTIVE_CERTIFICATION","result":None}
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/internal/shrimp-animation/bilibili-post-unfreeze-observation-evaluate",
    include_in_schema=False,
)
def shrimp_animation_bilibili_post_unfreeze_observation_evaluate(
    authorization: str | None = Header(default=None,alias="Authorization"),
    x_shrimp_health_monitor_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Health-Monitor-Key",
    ),
):
    provided=(x_shrimp_health_monitor_key or "").strip()
    bearer=(authorization or "").strip()
    expected=settings.shrimp_bilibili_health_monitor_key.strip()
    cron=settings.cron_secret.strip()
    manual_ok=bool(expected) and secrets.compare_digest(provided,expected)
    cron_ok=bool(cron) and bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:].strip(),cron
    )
    if not (manual_ok or cron_ok):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili observation evaluator authorization",
        )
    try:
        return {
            "status":"EVALUATED",
            "result":evaluate_observation(
                actor="scheduled-bilibili-post-unfreeze-observation"
            ),
        }
    except RuntimeError as exc:
        if "No active post-unfreeze observation session" in str(exc):
            return {"status":"NO_ACTIVE_OBSERVATION","result":None}
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/internal/shrimp-animation/bilibili-reliability-analysis",
    include_in_schema=False,
)
def shrimp_animation_bilibili_reliability_analysis(
    authorization: str | None = Header(default=None,alias="Authorization"),
    x_shrimp_health_monitor_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Health-Monitor-Key",
    ),
):
    provided=(x_shrimp_health_monitor_key or "").strip()
    bearer=(authorization or "").strip()
    expected=settings.shrimp_bilibili_health_monitor_key.strip()
    cron=settings.cron_secret.strip()
    manual_ok=bool(expected) and secrets.compare_digest(provided,expected)
    cron_ok=bool(cron) and bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:].strip(),cron
    )
    if not (manual_ok or cron_ok):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili reliability analysis authorization",
        )
    return {
        "status":"ANALYZED",
        "result":run_reliability_analysis(
            actor="scheduled-bilibili-reliability-analysis"
        ),
    }


@app.post(
    "/internal/shrimp-animation/bilibili-reliability-scorecard",
    include_in_schema=False,
)
def shrimp_animation_bilibili_reliability_scorecard(
    authorization: str | None = Header(default=None,alias="Authorization"),
    x_shrimp_health_monitor_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Health-Monitor-Key",
    ),
):
    provided=(x_shrimp_health_monitor_key or "").strip()
    bearer=(authorization or "").strip()
    expected=settings.shrimp_bilibili_health_monitor_key.strip()
    cron=settings.cron_secret.strip()
    manual_ok=bool(expected) and secrets.compare_digest(provided,expected)
    cron_ok=bool(cron) and bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:].strip(),cron
    )
    if not (manual_ok or cron_ok):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili reliability scorecard authorization",
        )
    return {
        "status":"GENERATED",
        "scorecards":generate_reliability_scorecards(
            actor="scheduled-bilibili-reliability"
        ),
    }


@app.post(
    "/internal/shrimp-animation/bilibili-daily-quota-audit",
    include_in_schema=False,
)
def shrimp_animation_bilibili_daily_quota_audit(
    authorization: str | None = Header(default=None,alias="Authorization"),
    x_shrimp_health_monitor_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Health-Monitor-Key",
    ),
):
    provided=(x_shrimp_health_monitor_key or "").strip()
    bearer=(authorization or "").strip()
    expected=settings.shrimp_bilibili_health_monitor_key.strip()
    cron=settings.cron_secret.strip()
    manual_ok=bool(expected) and secrets.compare_digest(provided,expected)
    cron_ok=bool(cron) and bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:].strip(),cron
    )
    if not (manual_ok or cron_ok):
        raise HTTPException(
            status_code=403,
            detail="Invalid Bilibili quota audit authorization",
        )
    return {
        "status":"AUDITED",
        "accounts":run_daily_quota_audit(
            actor="scheduled-bilibili-daily-quota-audit"
        ),
    }


@app.post("/internal/shrimp-animation/bilibili-health-monitor", include_in_schema=False)
def shrimp_animation_bilibili_health_monitor(
    authorization: str | None = Header(default=None,alias="Authorization"),
    x_shrimp_health_monitor_key: str | None = Header(default=None,alias="X-Shrimp-Health-Monitor-Key"),
):
    provided=(x_shrimp_health_monitor_key or "").strip()
    bearer=(authorization or "").strip()
    expected=settings.shrimp_bilibili_health_monitor_key.strip()
    cron=settings.cron_secret.strip()
    manual_ok=bool(expected) and secrets.compare_digest(provided,expected)
    cron_ok=bool(cron) and bearer.startswith("Bearer ") and secrets.compare_digest(
        bearer[7:].strip(),cron
    )
    if not (manual_ok or cron_ok):
        raise HTTPException(status_code=403,detail="Invalid Bilibili health monitor authorization")
    return run_health_monitor(actor="scheduled-bilibili-health-monitor")


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
        "shrimp_publish_authorization_gate":(
            "DISABLED"
            if not settings.shrimp_publish_authorization_key.strip()
            else (
                "ENABLED"
                if _shrimp_publish_key_independent()
                else "MISCONFIGURED"
            )
        ),
        "shrimp_publish_execution_gate":(
            "DISABLED"
            if not settings.shrimp_publish_execution_key.strip()
            else (
                "ENABLED"
                if _shrimp_publish_execution_key_independent()
                else "MISCONFIGURED"
            )
        ),
        "shrimp_publish_executor_enabled":settings.shrimp_publish_executor_enabled,
        "shrimp_publish_execution_adapter":(
            settings.shrimp_publish_execution_adapter.strip().upper()
            or "MOCK"
        ),
        "shrimp_bilibili_live_acceptance_gate":(
            "DISABLED"
            if not settings.shrimp_bilibili_live_acceptance_key.strip()
            else (
                "ENABLED"
                if _shrimp_bilibili_live_acceptance_key_independent()
                else "MISCONFIGURED"
            )
        ),
        "shrimp_bilibili_live_acceptance_enabled":
            settings.shrimp_bilibili_live_acceptance_enabled,
        "shrimp_bilibili_cookie_credentials_present":all((
            settings.shrimp_bilibili_sessdata.strip(),
            settings.shrimp_bilibili_bili_jct.strip(),
            settings.shrimp_bilibili_dede_user_id.strip(),
        )),
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
        "shrimp_human_review_workspace":"ENABLED",
        "shrimp_human_review_gate":(
            "ENABLED"
            if settings.shrimp_human_review_key.strip()
            else "DISABLED"
        ),
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

@app.get("/v1/production-providers")
def production_providers(limit: int = 100, asset_class: str | None = None):
    try:
        return list_provider_definitions(limit=limit,asset_class=asset_class)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc

@app.get("/v1/production-provider-jobs")
def production_provider_jobs(
    limit: int = 100,
    status: str | None = None,
    provider_key: str | None = None,
):
    try:
        return list_provider_jobs(
            limit=limit,
            status=status,
            provider_key=provider_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc

@app.get("/v1/production-provider-jobs/{job_id}")
def production_provider_job(job_id: UUID):
    try:
        return get_provider_job(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc

@app.get("/v1/shrimp-animation/jobs/{job_id}")
def shrimp_animation_job(job_id: UUID):
    try:
        return get_shrimp_animation_job(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc

@app.get("/v1/shrimp-animation/jobs/{job_id}/resource-plans")
def shrimp_animation_resource_plans(
    job_id: UUID,
    include_stale: bool = False,
):
    try:
        get_shrimp_animation_job(job_id)
        return list_resource_plans(job_id,include_stale=include_stale)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc

@app.get("/v1/shrimp-animation/jobs/{job_id}/artifacts")
def shrimp_animation_artifacts(
    job_id: UUID,
    include_superseded: bool = False,
):
    try:
        get_shrimp_animation_job(job_id)
        return list_shrimp_artifacts(
            job_id,
            include_superseded=include_superseded,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc

@app.get("/v1/shrimp-animation/jobs/{job_id}/compositions")
def shrimp_animation_compositions(
    job_id: UUID,
    include_stale: bool = False,
):
    try:
        get_shrimp_animation_job(job_id)
        return list_animation_compositions(
            job_id,
            include_stale=include_stale,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/pipeline-console")
def shrimp_animation_pipeline_console(limit: int = 100):
    jobs=list_provider_jobs(
        limit=max(1,min(int(limit),200)),
        provider_key="shrimp_animation",
    )
    if not jobs:
        return {
            "mode":"READ_ONLY_PIPELINE_CONSOLE",
            "total_jobs":0,
            "jobs":[],
            "secrets_redacted":True,
        }

    job_ids=[str(row["id"]) for row in jobs]
    with engine.connect() as db:
        details=db.execute(
            text("""
              SELECT saj.provider_job_id,saj.episode_id,saj.review_status,
                     saj.render_artifact_sha256,saj.qc_report_sha256,
                     saj.episode_bundle_sha256,
                     COUNT(DISTINCT pp.id) AS publish_plan_count,
                     COUNT(DISTINCT pe.id) AS publish_execution_count
              FROM shrimp_animation_jobs saj
              LEFT JOIN shrimp_animation_publish_plans pp
                ON pp.provider_job_id=saj.provider_job_id
              LEFT JOIN shrimp_animation_publish_executions pe
                ON pe.provider_job_id=saj.provider_job_id
              WHERE saj.provider_job_id=ANY(CAST(:job_ids AS uuid[]))
              GROUP BY saj.provider_job_id,saj.episode_id,saj.review_status,
                       saj.render_artifact_sha256,saj.qc_report_sha256,
                       saj.episode_bundle_sha256
            """),
            {"job_ids":job_ids},
        ).mappings().all()
    detail_by_id={str(row["provider_job_id"]):dict(row) for row in details}
    items=[]
    for row in jobs:
        item=dict(row)
        item["id"]=str(item["id"])
        extra=detail_by_id.get(item["id"],{})
        item.update({
            "episode_id":extra.get("episode_id"),
            "review_status":extra.get("review_status"),
            "render_ready":bool(extra.get("render_artifact_sha256")),
            "qc_ready":bool(extra.get("qc_report_sha256")),
            "package_ready":bool(extra.get("episode_bundle_sha256")),
            "publish_plan_count":int(extra.get("publish_plan_count") or 0),
            "publish_execution_count":int(
                extra.get("publish_execution_count") or 0
            ),
        })
        items.append(item)
    return {
        "mode":"READ_ONLY_PIPELINE_CONSOLE",
        "total_jobs":len(items),
        "jobs":items,
        "secrets_redacted":True,
    }


@app.get("/v1/shrimp-animation/jobs/{job_id}/pipeline-console")
def shrimp_animation_pipeline_console_job(job_id: UUID):
    try:
        job=get_shrimp_animation_job(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc

    resource_plans=list_resource_plans(job_id,include_stale=True)
    artifacts=list_shrimp_artifacts(
        job_id,
        include_superseded=True,
    )
    compositions=list_animation_compositions(
        job_id,
        include_stale=True,
    )
    try:
        review=get_shrimp_review_workspace(job_id)
        review_error=None
    except (LookupError,RuntimeError,FileNotFoundError) as exc:
        review_error=str(exc)
        shrimp=job.get("shrimp_animation",{})
        with engine.connect() as review_db:
            decisions=[
                _control_center_row(row)
                for row in review_db.execute(
                    text("""
                      SELECT decision,reason,actor,decision_status,
                             decision_sha256,created_at
                      FROM shrimp_animation_review_decisions
                      WHERE provider_job_id=CAST(:job_id AS uuid)
                      ORDER BY created_at DESC,id DESC
                    """),
                    {"job_id":job_id},
                ).mappings().all()
            ]
        review={
            "episode_id":shrimp.get("episode_id"),
            "title":shrimp.get("episode_id"),
            "review_status":shrimp.get("review_status"),
            "job_status":job.get("job_status"),
            "qc_passed":bool(shrimp.get("qc_report_sha256")),
            "episode_bundle_sha256":shrimp.get("episode_bundle_sha256"),
            "release_review_package_sha256":
                shrimp.get("release_review_package_sha256"),
            "render_artifact_sha256":shrimp.get("render_artifact_sha256"),
            "decisions":decisions,
            "artifact_files_available":False,
            "fallback_summary":True,
        }

    plans=list_publish_plans(job_id)
    executions=list_publish_executions(job_id)

    with engine.connect() as db:
        acceptance_rows=db.execute(
            text("""
              SELECT a.id,a.execution_id,a.acceptance_status,
                     a.provider_read_back_verified,
                     a.private_visibility_verified,
                     a.cleanup_outcome,a.cleanup_verified,
                     a.production_account_touched,
                     a.public_visibility_observed,
                     a.created_at,a.updated_at,a.finished_at
              FROM shrimp_animation_bilibili_live_acceptance_runs a
              JOIN shrimp_animation_publish_executions e
                ON e.id=a.execution_id
              WHERE e.provider_job_id=CAST(:job_id AS uuid)
              ORDER BY a.created_at DESC,a.id DESC
            """),
            {"job_id":job_id},
        ).mappings().all()

    shrimp=job.get("shrimp_animation",{})
    evidence={
        "brief_sha256":shrimp.get("brief_sha256"),
        "story_sha256":shrimp.get("story_sha256"),
        "script_sha256":shrimp.get("script_sha256"),
        "scene_sha256":shrimp.get("scene_sha256"),
        "asset_plan_sha256":shrimp.get("asset_plan_sha256"),
        "voice_plan_sha256":shrimp.get("voice_plan_sha256"),
        "assets_manifest_sha256":shrimp.get("assets_manifest_sha256"),
        "voices_manifest_sha256":shrimp.get("voices_manifest_sha256"),
        "animation_manifest_sha256":shrimp.get("animation_manifest_sha256"),
        "remotion_props_sha256":shrimp.get("remotion_props_sha256"),
        "render_artifact_sha256":shrimp.get("render_artifact_sha256"),
        "qc_report_sha256":shrimp.get("qc_report_sha256"),
        "episode_bundle_sha256":shrimp.get("episode_bundle_sha256"),
        "release_review_package_sha256":
            shrimp.get("release_review_package_sha256"),
    }
    return {
        "mode":"READ_ONLY_PIPELINE_JOB",
        "job":job,
        "resource_plans":resource_plans,
        "artifacts":artifacts,
        "compositions":compositions,
        "review":review,
        "review_error":review_error,
        "publish_plans":plans,
        "publish_executions":executions,
        "bilibili_acceptances":[
            _control_center_row(row) for row in acceptance_rows
        ],
        "evidence":evidence,
        "safety":{
            "external_side_effects":job.get("external_side_effects"),
            "production_execution_enabled":bool(
                job.get("production_execution_enabled")
            ),
            "publish_enabled":bool(job.get("publish_enabled")),
            "console_write_actions":False,
        },
        "secrets_redacted":True,
    }


@app.get("/v1/shrimp-animation/audit-evidence")
def shrimp_animation_audit_evidence(
    category: str | None = None,
    status: str | None = None,
    q: str | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 200,
    offset: int = 0,
):
    try:
        return audit_evidence_explorer(
            category=category,
            status=status,
            q=q,
            after=after,
            before=before,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/audit-evidence/export")
def shrimp_animation_audit_evidence_export(
    format: str = "json",
    category: str | None = None,
    status: str | None = None,
    q: str | None = None,
    after: str | None = None,
    before: str | None = None,
):
    try:
        content,media_type,filename=export_audit_evidence(
            format=format,
            category=category,
            status=status,
            q=q,
            after=after,
            before=before,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition":f'attachment; filename="{filename}"',
            "Cache-Control":"no-store",
            "X-Content-Type-Options":"nosniff",
        },
    )


@app.get("/v1/shrimp-animation/trust-governance-console")
def shrimp_animation_trust_governance_console():
    def section(loader):
        try:
            return {"available":True,"data":loader()}
        except Exception as exc:
            return {
                "available":False,
                "error_type":type(exc).__name__,
            }

    sections={
        "signing":section(signing_key_lifecycle_dashboard),
        "multisigner":section(multisigner_dashboard),
        "hsm":section(hsm_root_custody_dashboard),
        "external_verification":section(external_verification_dashboard),
        "transparency":section(transparency_dashboard),
        "external_kms":section(external_kms_dashboard),
        "live_cloud_kms":section(live_cloud_kms_dashboard),
        "certification":section(certification_dashboard),
        "renewal":section(renewal_dashboard),
        "trust_audit":section(trust_audit_dashboard),
        "governance":section(governance_dashboard),
    }

    signing=sections["signing"].get("data") or {}
    multisigner=sections["multisigner"].get("data") or {}
    hsm=sections["hsm"].get("data") or {}
    external_kms=sections["external_kms"].get("data") or {}
    live_cloud=sections["live_cloud_kms"].get("data") or {}
    certification=sections["certification"].get("data") or {}
    renewal=sections["renewal"].get("data") or {}
    trust_audit=sections["trust_audit"].get("data") or {}
    governance=sections["governance"].get("data") or {}
    transparency=sections["transparency"].get("data") or {}

    root_chain=signing.get("trust_root_chain") or {}
    current_certification=(
        renewal.get("current_certification")
        or certification.get("current_certification")
        or {}
    )
    current_review=governance.get("current_review") or {}
    accepted_clouds=live_cloud.get("accepted_provider_types") or []

    trust_status=(
        "VERIFIED"
        if root_chain.get("verification_status")=="PASS"
        and int(signing.get("active_key_count") or 0)>0
        else "ATTENTION"
    )
    cloud_status=(
        "CROSS_CLOUD_ACCEPTED"
        if live_cloud.get("live_cross_cloud_acceptance_completed")
        else "PARTIAL"
        if accepted_clouds
        else "NOT_LIVE_ACCEPTED"
    )
    certification_status=(
        current_certification.get("certification_status")
        or ("CURRENT" if renewal.get("certification_current") else "NOT_CERTIFIED")
    )
    governance_status=(
        current_review.get("review_status")
        or "NORMAL"
    )

    return {
        "mode":"READ_ONLY_TRUST_GOVERNANCE_CONSOLE",
        "status":{
            "trust":trust_status,
            "cloud_kms":cloud_status,
            "certification":certification_status,
            "governance":governance_status,
        },
        "summary":{
            "active_signing_keys":int(signing.get("active_key_count") or 0),
            "trust_root_count":len(signing.get("trust_roots") or []),
            "root_transition_count":len(multisigner.get("plans") or []),
            "hsm_key_count":len(hsm.get("keys") or []),
            "hsm_ceremony_count":len(hsm.get("ceremonies") or []),
            "external_kms_provider_count":len(
                external_kms.get("providers") or []
            ),
            "accepted_cloud_provider_types":accepted_clouds,
            "live_cloud_acceptance_count":len(
                live_cloud.get("acceptances") or []
            ),
            "cross_cloud_ceremony_count":len(
                live_cloud.get("cross_cloud_ceremonies") or []
            ),
            "dsse_attestation_count":int(
                (transparency.get("counts") or {}).get("attestations") or 0
            ),
            "transparency_entry_count":int(
                (transparency.get("counts") or {}).get(
                    "transparency_entries"
                ) or 0
            ),
            "certification_current":bool(
                renewal.get("certification_current")
            ),
            "certification_expires_in_days":
                renewal.get("expires_in_days"),
            "recertification_required":bool(
                renewal.get("recertification_required")
            ),
            "trust_chain_valid":bool(
                trust_audit.get("trust_chain_valid")
            ),
            "governance_review_count":len(
                governance.get("reviews") or []
            ),
            "policy_intent_count":len(
                governance.get("policy_intents") or []
            ),
        },
        "sections":sections,
        "safety":{
            "read_only":True,
            "cloud_kms_execution":False,
            "signing_key_rotation":False,
            "root_transition_apply":False,
            "policy_application":False,
            "production_writes":False,
            "provider_writes":False,
            "private_key_export":False,
        },
        "references":{
            "trust_root":"theupdateframework/python-tuf",
            "signature_verification":
                "secure-systems-lab/securesystemslib",
            "attestation":"in-toto/attestation",
            "transparency":"sigstore/rekor",
            "private_key_custody":"openbao/openbao",
            "pkcs11":"pyauth/python-pkcs11",
        },
        "secrets_redacted":True,
    }


@app.get("/v1/shrimp-animation/control-center/final-acceptance")
def shrimp_animation_control_center_final_acceptance():
    try:
        summary=_shrimp_control_center_summary()
        trust=shrimp_animation_trust_governance_console()
        operations=operations_console()
        audit=audit_evidence_explorer(limit=500,offset=0)
        return evaluate_unified_control_center_acceptance(
            summary=summary,
            trust=trust,
            operations=operations,
            audit=audit,
        )
    except DBAPIError as exc:
        if is_database_unavailable(exc):
            raise HTTPException(
                status_code=503,
                detail="Unified Control Center acceptance database is unavailable",
            ) from exc
        raise


@app.get("/v1/shrimp-animation/review-workspace")
def shrimp_animation_review_workspace(limit: int = 100):
    return list_shrimp_review_workspace(limit=limit)


@app.get("/v1/shrimp-animation/review-workspace/{job_id}")
def shrimp_animation_review_workspace_job(job_id: UUID):
    try:
        return get_shrimp_review_workspace(job_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/review-workspace/{job_id}/episode")
def shrimp_animation_review_episode(job_id: UUID):
    try:
        item=get_episode_player_file(job_id)
        return FileResponse(
            item.path,
            media_type=item.media_type,
            headers={
                "Cache-Control":"no-store",
                "X-Content-SHA256":item.sha256,
                "X-Content-Type-Options":"nosniff",
            },
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except (RuntimeError,PermissionError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/review-workspace/{job_id}/bundle")
def shrimp_animation_review_bundle(job_id: UUID):
    try:
        item=get_episode_bundle_file(job_id)
        return FileResponse(
            item.path,
            media_type=item.media_type,
            filename=item.filename,
            headers={
                "Cache-Control":"no-store",
                "X-Content-SHA256":item.sha256,
                "X-Content-Type-Options":"nosniff",
            },
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except (RuntimeError,PermissionError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get(
    "/v1/shrimp-animation/review-workspace/{job_id}/review-document"
)
def shrimp_animation_review_document(job_id: UUID):
    try:
        item=get_review_document_file(job_id)
        return FileResponse(
            item.path,
            media_type=item.media_type,
            headers={
                "Cache-Control":"no-store",
                "X-Content-SHA256":item.sha256,
                "X-Content-Type-Options":"nosniff",
            },
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except (RuntimeError,PermissionError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/review-workspace/{job_id}/decision"
)
def shrimp_animation_review_decision(
    job_id: UUID,
    payload: ShrimpHumanReviewDecision,
    x_shrimp_review_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Review-Key",
    ),
):
    _require_shrimp_review_key(x_shrimp_review_key)
    try:
        return decide_shrimp_release(
            job_id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
            episode_bundle_sha256=payload.episode_bundle_sha256,
            release_review_package_sha256=(
                payload.release_review_package_sha256
            ),
            confirmed_checklist=payload.confirmed_checklist,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc



@app.get("/v1/shrimp-animation/bilibili-certification-trust-audit")
def shrimp_animation_bilibili_certification_trust_audit():
    return trust_audit_dashboard()


@app.get("/v1/shrimp-animation/bilibili-certification-integrity-audits")
def shrimp_animation_bilibili_certification_integrity_audits(limit: int = 100):
    return list_integrity_audits(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-renewal-sla-escalations")
def shrimp_animation_bilibili_renewal_sla_escalations(limit: int = 100):
    return list_renewal_escalations(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-certification-audit-proofs")
def shrimp_animation_bilibili_certification_audit_proofs(limit: int = 100):
    return list_audit_proofs(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-certification-audit-proof/latest")
def shrimp_animation_bilibili_certification_audit_proof_latest():
    proofs=list_audit_proofs(limit=1)
    if not proofs:
        raise HTTPException(
            status_code=404,
            detail="No certification audit proof has been generated yet",
        )
    return proofs[0]


@app.get("/v1/shrimp-animation/bilibili-external-verification")
def shrimp_animation_bilibili_external_verification():
    return external_verification_dashboard()


@app.get("/v1/shrimp-animation/bilibili-signing-key-lifecycle")
def shrimp_animation_bilibili_signing_key_lifecycle():
    return signing_key_lifecycle_dashboard()


@app.get("/v1/shrimp-animation/bilibili-signing-keys")
def shrimp_animation_bilibili_signing_keys(limit: int = 100):
    return list_signing_key_states(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-signing-key-events")
def shrimp_animation_bilibili_signing_key_events(limit: int = 200):
    return list_signing_key_events(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-signing-trust-roots")
def shrimp_animation_bilibili_signing_trust_roots(limit: int = 100):
    return list_trust_roots(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-signing-trust-roots/verify")
def shrimp_animation_bilibili_signing_trust_roots_verify():
    return verify_trust_root_chain()


@app.get("/v1/shrimp-animation/bilibili-signing-key-lifecycle/verify-all")
def shrimp_animation_bilibili_signing_verify_all(limit: int = 500):
    return verify_all_bundles_with_key_registry(limit=limit)


@app.get(
    "/v1/shrimp-animation/bilibili-audit-proof-bundles/"
    "{bundle_id}/verify-signing-trust"
)
def shrimp_animation_bilibili_bundle_verify_signing_trust(bundle_id: UUID):
    try:
        return verify_bundle_with_key_registry(bundle_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/bilibili-signing-key-lifecycle/bootstrap",
    status_code=201,
)
def shrimp_animation_bilibili_signing_trust_bootstrap(
    payload: ShrimpBilibiliSigningTrustBootstrap,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Reliability-Governance-Key",
    ),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return bootstrap_signing_trust(
            actor=payload.actor,
            key_label=payload.key_label,
        )
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/bilibili-signing-key-lifecycle/rotate",
    status_code=201,
)
def shrimp_animation_bilibili_signing_key_rotate(
    payload: ShrimpBilibiliSigningKeyRotation,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Signing-Key-Rotation-Key",
    ),
):
    _require_shrimp_bilibili_signing_key_rotation_key(x_key)
    try:
        return rotate_signing_key(
            actor=payload.actor,
            reason=payload.reason,
            key_label=payload.key_label,
        )
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/bilibili-signing-keys/{fingerprint}/revoke",
    status_code=201,
)
def shrimp_animation_bilibili_signing_key_revoke(
    fingerprint: str,
    payload: ShrimpBilibiliSigningKeyRevocation,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Signing-Key-Rotation-Key",
    ),
):
    _require_shrimp_bilibili_signing_key_rotation_key(x_key)
    try:
        return revoke_signing_key(
            fingerprint,
            effective_at=payload.effective_at,
            reason=payload.reason,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-root-transitions")
def shrimp_animation_bilibili_root_transitions():
    return multisigner_dashboard()


@app.post("/v1/shrimp-animation/bilibili-root-transitions",status_code=201)
def shrimp_animation_bilibili_root_transition_create(
    payload: ShrimpBilibiliRootTransitionPlanCreate,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Signing-Key-Rotation-Key"),
):
    _require_shrimp_bilibili_signing_key_rotation_key(x_key)
    try:
        return create_root_transition_plan(
            candidate_fingerprints=payload.candidate_fingerprints,
            candidate_threshold=payload.candidate_threshold,
            transition_type=payload.transition_type,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-root-transitions/{plan_id}")
def shrimp_animation_bilibili_root_transition(plan_id: UUID):
    try:
        plan=get_transition_plan(plan_id)
        return {
            "plan":plan,
            "signature_status":transition_signature_status(plan_id),
            "approval_status":transition_approval_status(plan_id),
        }
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-root-transitions/{plan_id}/signatures",status_code=201)
def shrimp_animation_bilibili_root_transition_signature(
    plan_id: UUID,
    payload: ShrimpBilibiliRootTransitionSignature,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Signing-Key-Rotation-Key"),
):
    _require_shrimp_bilibili_signing_key_rotation_key(x_key)
    try:
        return add_transition_signature(
            plan_id,
            fingerprint=payload.fingerprint,
            signature_b64=payload.signature_b64,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-root-transitions/{plan_id}/approve-a",status_code=201)
def shrimp_animation_bilibili_root_transition_approve_a(
    plan_id: UUID,
    payload: ShrimpBilibiliRootTransitionDecision,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Root-Transition-Approver-A-Key"),
):
    approver=_require_shrimp_bilibili_root_transition_approver("A",x_key)
    try:
        return decide_transition(
            plan_id,decision=payload.decision,reason=payload.reason,approver=approver
        )
    except (LookupError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-root-transitions/{plan_id}/approve-b",status_code=201)
def shrimp_animation_bilibili_root_transition_approve_b(
    plan_id: UUID,
    payload: ShrimpBilibiliRootTransitionDecision,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Root-Transition-Approver-B-Key"),
):
    approver=_require_shrimp_bilibili_root_transition_approver("B",x_key)
    try:
        return decide_transition(
            plan_id,decision=payload.decision,reason=payload.reason,approver=approver
        )
    except (LookupError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-root-transitions/{plan_id}/apply",status_code=201)
def shrimp_animation_bilibili_root_transition_apply(
    plan_id: UUID,
    payload: ShrimpBilibiliRootTransitionApply,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Signing-Key-Rotation-Key"),
):
    _require_shrimp_bilibili_signing_key_rotation_key(x_key)
    try:
        return apply_root_transition(plan_id,actor=payload.actor)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-openbao-live-acceptances")
def shrimp_animation_bilibili_openbao_live_acceptances(limit: int = 100):
    return list_openbao_live_acceptances(limit=limit)


@app.post("/v1/shrimp-animation/bilibili-openbao-live-acceptances",status_code=201)
def shrimp_animation_bilibili_openbao_live_acceptance(
    payload: ShrimpBilibiliOpenBaoLiveAcceptance,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Signing-Key-Rotation-Key"),
):
    _require_shrimp_bilibili_signing_key_rotation_key(x_key)
    if not settings.shrimp_bilibili_openbao_live_acceptance_enabled:
        raise HTTPException(status_code=409,detail="OpenBao live acceptance is disabled")
    if not settings.shrimp_bilibili_openbao_url.strip() or not settings.shrimp_bilibili_openbao_token.strip():
        raise HTTPException(status_code=409,detail="OpenBao live acceptance is not configured")
    return run_openbao_live_acceptance(
        base_url=settings.shrimp_bilibili_openbao_url,
        token=settings.shrimp_bilibili_openbao_token,
        key_name=settings.shrimp_bilibili_openbao_key_name,
        actor=payload.actor,
    )


@app.get("/v1/shrimp-animation/bilibili-key-compromise-recovery-drills")
def shrimp_animation_bilibili_key_compromise_recovery_drills(limit: int = 100):
    return list_key_compromise_recovery_drills(limit=limit)


@app.post("/v1/shrimp-animation/bilibili-key-compromise-recovery-drills",status_code=201)
def shrimp_animation_bilibili_key_compromise_recovery_drill(
    payload: ShrimpBilibiliCompromiseRecoveryDrill,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Signing-Key-Rotation-Key"),
):
    _require_shrimp_bilibili_signing_key_rotation_key(x_key)
    try:
        return run_key_compromise_recovery_drill(
            compromised_fingerprint=payload.compromised_fingerprint,
            affected_bundle_ids=payload.affected_bundle_ids,
            actor=payload.actor,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-live-cloud-kms")
def shrimp_animation_bilibili_live_cloud_kms():
    return live_cloud_kms_dashboard()


@app.get("/v1/shrimp-animation/bilibili-live-cloud-kms/readiness")
def shrimp_animation_bilibili_live_cloud_kms_readiness():
    return live_cloud_identity_readiness()


@app.post("/v1/shrimp-animation/bilibili-live-cloud-kms/accept",status_code=201)
def shrimp_animation_bilibili_live_cloud_kms_accept(
    payload: ShrimpBilibiliLiveCloudKmsAcceptanceAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Root-Ceremony-Key"),
):
    _require_shrimp_bilibili_root_ceremony_key(x_key)
    try:
        return run_live_provider_acceptance(
            payload.provider_type,actor=payload.actor
        )
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-live-cloud-kms/cross-cloud",status_code=201)
def shrimp_animation_bilibili_live_cross_cloud_kms_accept(
    payload: ShrimpBilibiliLiveCrossCloudAcceptanceAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Root-Ceremony-Key"),
):
    _require_shrimp_bilibili_root_ceremony_key(x_key)
    try:
        return run_live_cross_cloud_acceptance(
            list(payload.provider_types),
            threshold=payload.threshold,
            actor=payload.actor,
        )
    except (RuntimeError,ValueError,LookupError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-external-kms")
def shrimp_animation_bilibili_external_kms():
    return external_kms_dashboard()


@app.post("/v1/shrimp-animation/bilibili-external-kms/sync",status_code=201)
def shrimp_animation_bilibili_external_kms_sync(
    payload: ShrimpBilibiliExternalKmsSyncAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Root-Ceremony-Key"),
):
    _require_shrimp_bilibili_root_ceremony_key(x_key)
    if not settings.shrimp_bilibili_external_kms_enabled:
        raise HTTPException(status_code=409,detail="External KMS is disabled")
    try:
        return sync_configured_provider_registry(actor=payload.actor)
    except Exception as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-cross-kms-root-ceremonies",status_code=201)
def shrimp_animation_bilibili_cross_kms_root_ceremony(
    payload: ShrimpBilibiliCrossKmsCeremonyAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Root-Ceremony-Key"),
):
    _require_shrimp_bilibili_root_ceremony_key(x_key)
    if not settings.shrimp_bilibili_external_kms_enabled:
        raise HTTPException(status_code=409,detail="External KMS is disabled")
    try:
        return create_cross_kms_root_ceremony(
            provider_refs=payload.provider_refs,
            threshold=payload.threshold,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-hsm-root-custody")
def shrimp_animation_bilibili_hsm_root_custody():
    return hsm_root_custody_dashboard()


@app.post("/v1/shrimp-animation/bilibili-hsm-root-custody/register",status_code=201)
def shrimp_animation_bilibili_hsm_register(
    payload: ShrimpBilibiliRootBackupAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Root-Ceremony-Key"),
):
    _require_shrimp_bilibili_root_ceremony_key(x_key)
    try:
        return register_current_hsm_key(actor=payload.actor)
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-root-ceremonies",status_code=201)
def shrimp_animation_bilibili_root_ceremony(
    payload: ShrimpBilibiliRootCeremonyAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Root-Ceremony-Key"),
):
    _require_shrimp_bilibili_root_ceremony_key(x_key)
    try:
        return create_root_ceremony(
            ceremony_type=payload.ceremony_type,
            actor=payload.actor,
        )
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-offline-root-backups",status_code=201)
def shrimp_animation_bilibili_offline_root_backup(
    payload: ShrimpBilibiliRootBackupAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Root-Ceremony-Key"),
):
    _require_shrimp_bilibili_root_ceremony_key(x_key)
    try:
        return generate_offline_root_backup(actor=payload.actor)
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-offline-root-backups/{backup_id}/restore-drill",status_code=201)
def shrimp_animation_bilibili_root_restore_drill(
    backup_id: UUID,
    payload: ShrimpBilibiliRootRestoreDrillAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Root-Ceremony-Key"),
):
    _require_shrimp_bilibili_root_ceremony_key(x_key)
    try:
        return run_restore_drill(backup_id,actor=payload.actor)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-transparency")
def shrimp_animation_bilibili_transparency():
    return transparency_dashboard()


@app.post("/v1/shrimp-animation/bilibili-dsse-attestations",status_code=201)
def shrimp_animation_bilibili_dsse_attestation_create(
    payload: ShrimpBilibiliDsseAttestationCreate,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Reliability-Governance-Key"),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return create_dsse_attestation(
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            subject_sha256=payload.subject_sha256,
            predicate=payload.predicate,
            actor=payload.actor,
        )
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-dsse-attestations/{attestation_id}/sign-current",status_code=201)
def shrimp_animation_bilibili_dsse_sign_current(
    attestation_id: UUID,
    payload: ShrimpBilibiliAttestationAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Reliability-Governance-Key"),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return sign_dsse_attestation_current(attestation_id,actor=payload.actor)
    except (LookupError,RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-dsse-attestations/{attestation_id}/signatures",status_code=201)
def shrimp_animation_bilibili_dsse_signature_add(
    attestation_id: UUID,
    payload: ShrimpBilibiliDsseSignatureCreate,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Reliability-Governance-Key"),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return add_dsse_signature(
            attestation_id,
            fingerprint=payload.fingerprint,
            signature_b64=payload.signature_b64,
            public_key_pem_b64=payload.public_key_pem_b64,
            actor=payload.actor,
        )
    except (LookupError,RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-dsse-attestations/{attestation_id}/verify")
def shrimp_animation_bilibili_dsse_verify(attestation_id: UUID):
    try:
        return verify_dsse_threshold(attestation_id)
    except (LookupError,RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-dsse-attestations/{attestation_id}/timestamp",status_code=201)
def shrimp_animation_bilibili_dsse_timestamp(
    attestation_id: UUID,
    payload: ShrimpBilibiliAttestationAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Reliability-Governance-Key"),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return request_trusted_timestamp(attestation_id,actor=payload.actor)
    except (LookupError,RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-dsse-attestations/{attestation_id}/rekor",status_code=201)
def shrimp_animation_bilibili_dsse_rekor(
    attestation_id: UUID,
    payload: ShrimpBilibiliAttestationAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Reliability-Governance-Key"),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return append_to_rekor(attestation_id,actor=payload.actor)
    except (LookupError,RuntimeError,ValueError,httpx.HTTPError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-dsse-attestations/{attestation_id}/rekor-compatible",status_code=201)
def shrimp_animation_bilibili_dsse_rekor_compatible(
    attestation_id: UUID,
    payload: ShrimpBilibiliAttestationAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Reliability-Governance-Key"),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return append_to_rekor_compatible(attestation_id,actor=payload.actor)
    except (LookupError,RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-dsse-attestations/{attestation_id}/verify-offline")
def shrimp_animation_bilibili_dsse_verify_offline(attestation_id: UUID):
    try:
        return verify_offline(attestation_id)
    except (LookupError,RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-dsse-attestations/{attestation_id}/offline-bundle",status_code=201)
def shrimp_animation_bilibili_dsse_offline_bundle(
    attestation_id: UUID,
    payload: ShrimpBilibiliAttestationAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Reliability-Governance-Key"),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return export_offline_bundle(attestation_id,actor=payload.actor)
    except (LookupError,RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-audit-proof-bundles")
def shrimp_animation_bilibili_audit_proof_bundles(limit: int = 100):
    return list_proof_bundles(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-audit-proof-bundles/{bundle_id}")
def shrimp_animation_bilibili_audit_proof_bundle(bundle_id: UUID):
    try:
        return get_proof_bundle(bundle_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-audit-proof-bundles/{bundle_id}/verify")
def shrimp_animation_bilibili_audit_proof_bundle_verify(bundle_id: UUID):
    try:
        return verify_proof_bundle(bundle_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-audit-proof-bundles",status_code=201)
def shrimp_animation_bilibili_audit_proof_bundle_create(
    payload: ShrimpBilibiliAuditBundleAction,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Reliability-Governance-Key",
    ),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return create_signed_proof_bundle(actor=payload.actor)
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/bilibili-audit-proof-bundles/{bundle_id}/anchors",
    status_code=201,
)
def shrimp_animation_bilibili_external_anchor_create(
    bundle_id: UUID,
    payload: ShrimpBilibiliExternalAnchorCreate,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Reliability-Governance-Key",
    ),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return register_external_anchor(
            bundle_id,
            anchor_provider=payload.anchor_provider,
            anchor_reference=payload.anchor_reference,
            anchor_digest_sha256=payload.anchor_digest_sha256,
            receipt=payload.receipt,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-external-verification-anchors")
def shrimp_animation_bilibili_external_anchors(limit: int = 100):
    return list_external_anchors(limit=limit)


@app.post(
    "/v1/shrimp-animation/bilibili-audit-proof-bundles/{bundle_id}/export",
    status_code=201,
)
def shrimp_animation_bilibili_export_registry_append(
    bundle_id: UUID,
    payload: ShrimpBilibiliExportRegistryAction,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Reliability-Governance-Key",
    ),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return append_export_registry(bundle_id,actor=payload.actor)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-audit-export-registry")
def shrimp_animation_bilibili_audit_export_registry(limit: int = 100):
    return list_export_registry(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-audit-export-registry/verify")
def shrimp_animation_bilibili_audit_export_registry_verify():
    return verify_export_registry_chain()


@app.get("/v1/shrimp-animation/bilibili-certification-renewal")
def shrimp_animation_bilibili_certification_renewal():
    return renewal_dashboard()


@app.get("/v1/shrimp-animation/bilibili-recertification-candidates")
def shrimp_animation_bilibili_recertification_candidates(limit: int = 100):
    return list_recertification_candidates(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-recertification-decisions")
def shrimp_animation_bilibili_recertification_decisions(limit: int = 100):
    return list_recertification_decisions(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-reliability-attestations")
def shrimp_animation_bilibili_reliability_attestations(limit: int = 200):
    return list_attestations(limit=limit)


@app.post(
    "/v1/shrimp-animation/bilibili-recertification-candidates/{candidate_id}/decision"
)
def shrimp_animation_bilibili_recertification_decision(
    candidate_id: UUID,
    payload: ShrimpBilibiliRecertificationDecision,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Recertification-Key",
    ),
):
    _require_shrimp_bilibili_recertification_key(x_key)
    try:
        return decide_recertification(
            candidate_id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
            candidate_sha256=payload.candidate_sha256,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-post-restore-certification")
def shrimp_animation_bilibili_post_restore_certification():
    return certification_dashboard()


@app.get("/v1/shrimp-animation/bilibili-post-restore-certifications")
def shrimp_animation_bilibili_post_restore_certifications(limit: int = 100):
    return list_certifications(limit=limit)


@app.get(
    "/v1/shrimp-animation/bilibili-post-restore-certifications/{certification_id}"
)
def shrimp_animation_bilibili_post_restore_certification_detail(
    certification_id: UUID,
):
    try:
        return get_certification(certification_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-certification-reopen-evaluations")
def shrimp_animation_bilibili_certification_reopen_evaluations(
    limit: int = 200,
):
    return list_reopen_evaluations(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-certification-reopen-events")
def shrimp_animation_bilibili_certification_reopen_events(limit: int = 100):
    return list_reopen_events(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-post-unfreeze-observation")
def shrimp_animation_bilibili_post_unfreeze_observation():
    return observation_dashboard()


@app.get("/v1/shrimp-animation/bilibili-post-unfreeze-observation/sessions")
def shrimp_animation_bilibili_observation_sessions(limit: int = 100):
    return list_observation_sessions(limit=limit)


@app.get(
    "/v1/shrimp-animation/bilibili-post-unfreeze-observation/sessions/{session_id}"
)
def shrimp_animation_bilibili_observation_session(session_id: UUID):
    try:
        return get_observation_session(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-post-unfreeze-ramp-evaluations")
def shrimp_animation_bilibili_post_unfreeze_ramp_evaluations(limit: int = 200):
    return list_ramp_evaluations(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-restore-acceptances")
def shrimp_animation_bilibili_restore_acceptances(limit: int = 100):
    return list_restore_acceptances(limit=limit)


@app.post(
    "/v1/shrimp-animation/bilibili-post-unfreeze-observation/"
    "sessions/{session_id}/accept"
)
def shrimp_animation_bilibili_restore_accept(
    session_id: UUID,
    payload: ShrimpBilibiliRestoreAcceptanceAction,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Restore-Acceptance-Key",
    ),
):
    _require_shrimp_bilibili_restore_acceptance_key(x_key)
    try:
        return accept_restore(session_id,actor=payload.actor)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-reliability-safe-unfreeze")
def shrimp_animation_bilibili_reliability_safe_unfreeze():
    return safe_unfreeze_dashboard()


@app.get("/v1/shrimp-animation/bilibili-reliability-restore-plans")
def shrimp_animation_bilibili_reliability_restore_plans(limit: int = 100):
    return list_restore_plans(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-reliability-restore-plans/{plan_id}")
def shrimp_animation_bilibili_reliability_restore_plan(plan_id: UUID):
    try:
        return get_restore_plan(plan_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-reliability-restore-approvals")
def shrimp_animation_bilibili_reliability_restore_approvals(limit: int = 100):
    return list_restore_approvals(limit=limit)


@app.post("/v1/shrimp-animation/bilibili-reliability-restore-plans")
def shrimp_animation_bilibili_reliability_restore_plan_create(
    payload: ShrimpBilibiliReliabilityRestorePlanCreate,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Reliability-Governance-Key",
    ),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return generate_restore_plan(actor=payload.actor)
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/bilibili-reliability-restore-plans/{plan_id}/first-approval"
)
def shrimp_animation_bilibili_reliability_restore_first_approval(
    plan_id: UUID,
    payload: ShrimpBilibiliReliabilityRestoreDecision,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Reliability-Restore-Approval-Key",
    ),
):
    _require_shrimp_bilibili_reliability_restore_approval_key(x_key)
    try:
        return first_restore_approval(
            plan_id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
            plan_sha256=payload.plan_sha256,
            dry_run_sha256=payload.dry_run_sha256,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/bilibili-reliability-restore-plans/{plan_id}/second-apply"
)
def shrimp_animation_bilibili_reliability_restore_second_apply(
    plan_id: UUID,
    payload: ShrimpBilibiliReliabilityRestoreDecision,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Reliability-Restore-Apply-Key",
    ),
):
    _require_shrimp_bilibili_reliability_restore_apply_key(x_key)
    try:
        return second_restore_apply(
            plan_id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
            plan_sha256=payload.plan_sha256,
            dry_run_sha256=payload.dry_run_sha256,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-reliability-policy-change")
def shrimp_animation_bilibili_reliability_policy_change():
    return policy_change_dashboard()


@app.get("/v1/shrimp-animation/bilibili-reliability-policy-control")
def shrimp_animation_bilibili_reliability_policy_control():
    return get_policy_control()


@app.get("/v1/shrimp-animation/bilibili-reliability-change-plans")
def shrimp_animation_bilibili_reliability_change_plans(limit: int = 100):
    return list_change_plans(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-reliability-change-plans/{plan_id}")
def shrimp_animation_bilibili_reliability_change_plan(plan_id: UUID):
    try:
        return get_change_plan(plan_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-reliability-change-apply-decisions")
def shrimp_animation_bilibili_reliability_change_apply_decisions(limit: int = 100):
    return list_apply_decisions(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-reliability-policy-control-events")
def shrimp_animation_bilibili_reliability_policy_control_events(limit: int = 100):
    return list_control_events(limit=limit)


@app.post(
    "/v1/shrimp-animation/bilibili-reliability-governance/intents/{intent_id}/change-plan"
)
def shrimp_animation_bilibili_reliability_change_plan_create(
    intent_id: UUID,
    payload: ShrimpBilibiliReliabilityChangePlanCreate,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Reliability-Governance-Key",
    ),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return generate_change_plan(intent_id,actor=payload.actor)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/bilibili-reliability-change-plans/{plan_id}/decision"
)
def shrimp_animation_bilibili_reliability_change_plan_decision(
    plan_id: UUID,
    payload: ShrimpBilibiliReliabilityChangeDecision,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Reliability-Policy-Apply-Key",
    ),
):
    _require_shrimp_bilibili_reliability_policy_apply_key(x_key)
    try:
        return decide_change_plan(
            plan_id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
            plan_sha256=payload.plan_sha256,
            dry_run_sha256=payload.dry_run_sha256,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-reliability-governance")
def shrimp_animation_bilibili_reliability_governance():
    return governance_dashboard()


@app.get("/v1/shrimp-animation/bilibili-reliability-governance/reviews")
def shrimp_animation_bilibili_reliability_governance_reviews(limit: int = 100):
    return list_governance_reviews(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-reliability-governance/reviews/{review_id}")
def shrimp_animation_bilibili_reliability_governance_review(review_id: UUID):
    try:
        return get_governance_review(review_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-reliability-governance/decisions")
def shrimp_animation_bilibili_reliability_governance_decisions(limit: int = 100):
    return list_governance_decisions(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-reliability-governance/intents")
def shrimp_animation_bilibili_reliability_governance_intents(limit: int = 100):
    return list_policy_intents(limit=limit)


@app.post(
    "/v1/shrimp-animation/bilibili-reliability-governance/reviews/{review_id}/decision"
)
def shrimp_animation_bilibili_reliability_governance_decision(
    review_id: UUID,
    payload: ShrimpBilibiliGovernanceDecision,
    x_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Reliability-Governance-Key",
    ),
):
    _require_shrimp_bilibili_reliability_governance_key(x_key)
    try:
        return decide_governance_review(
            review_id,
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


@app.get("/v1/shrimp-animation/bilibili-reliability-trend-dashboard")
def shrimp_animation_bilibili_reliability_trend_dashboard():
    return trend_dashboard()


@app.get("/v1/shrimp-animation/bilibili-reliability-trends")
def shrimp_animation_bilibili_reliability_trends(limit: int = 200):
    return list_trend_points(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-error-budget-burn")
def shrimp_animation_bilibili_error_budget_burn(limit: int = 100):
    return list_burn_rates(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-reliability-regressions")
def shrimp_animation_bilibili_reliability_regressions(
    status: str | None = None,
    limit: int = 100,
):
    return list_regressions(status=status,limit=limit)


@app.get("/v1/shrimp-animation/bilibili-reliability-policy-recommendations")
def shrimp_animation_bilibili_reliability_policy_recommendations(
    limit: int = 100,
):
    return list_policy_recommendations(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-reliability-dashboard")
def shrimp_animation_bilibili_reliability_dashboard():
    return reliability_dashboard()


@app.get("/v1/shrimp-animation/bilibili-reliability-scorecards")
def shrimp_animation_bilibili_reliability_scorecards(limit: int = 100):
    return list_reliability_scorecards(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-recurrence-clusters")
def shrimp_animation_bilibili_recurrence_clusters(limit: int = 100):
    return list_recurrence_clusters(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-incident-ops-summary")
def shrimp_animation_bilibili_incident_ops_summary():
    return incident_ops_summary()


@app.get("/v1/shrimp-animation/bilibili-oncall-routes")
def shrimp_animation_bilibili_oncall_routes(limit: int = 100):
    return list_oncall_routes(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-incident-sla-events")
def shrimp_animation_bilibili_incident_sla_events(limit: int = 100):
    return list_incident_sla_events(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-post-incident-reviews")
def shrimp_animation_bilibili_post_incident_reviews(limit: int = 100):
    return list_post_incident_reviews(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-corrective-actions")
def shrimp_animation_bilibili_corrective_actions(limit: int = 100):
    return list_corrective_actions(limit=limit)


@app.post("/v1/shrimp-animation/bilibili-incidents/{incident_id}/acknowledge")
def shrimp_animation_bilibili_incident_acknowledge(
    incident_id: UUID,
    payload: ShrimpBilibiliIncidentAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Bilibili-Incident-Ops-Key"),
):
    _require_shrimp_bilibili_incident_ops_key(x_key)
    try:
        return acknowledge_incident(incident_id,actor=payload.actor)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-incidents/{incident_id}/owner")
def shrimp_animation_bilibili_incident_owner(
    incident_id: UUID,
    payload: ShrimpBilibiliIncidentOwnerAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Bilibili-Incident-Ops-Key"),
):
    _require_shrimp_bilibili_incident_ops_key(x_key)
    try:
        return assign_incident_owner(
            incident_id,
            owner_ref=payload.owner_ref,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-incidents/{incident_id}/pir/complete")
def shrimp_animation_bilibili_pir_complete(
    incident_id: UUID,
    payload: ShrimpBilibiliPirComplete,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Bilibili-Incident-Ops-Key"),
):
    _require_shrimp_bilibili_incident_ops_key(x_key)
    try:
        return update_pir(
            incident_id,
            root_cause=payload.root_cause,
            lessons_learned=payload.lessons_learned,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-incidents/{incident_id}/corrective-actions")
def shrimp_animation_bilibili_corrective_action_create(
    incident_id: UUID,
    payload: ShrimpBilibiliCorrectiveActionCreate,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Bilibili-Incident-Ops-Key"),
):
    _require_shrimp_bilibili_incident_ops_key(x_key)
    try:
        return add_corrective_action(
            incident_id,
            description=payload.description,
            owner_ref=payload.owner_ref,
            due_at=payload.due_at,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-oncall-routes")
def shrimp_animation_bilibili_oncall_route_update(
    payload: ShrimpBilibiliOncallRouteUpdate,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Bilibili-Incident-Ops-Key"),
):
    _require_shrimp_bilibili_incident_ops_key(x_key)
    try:
        return configure_oncall_route(
            severity=payload.severity,
            owner_ref=payload.owner_ref,
            secondary_owner_ref=payload.secondary_owner_ref,
            actor=payload.actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-corrective-actions/{action_id}/complete")
def shrimp_animation_bilibili_corrective_action_complete(
    action_id: UUID,
    payload: ShrimpBilibiliCorrectiveActionComplete,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Bilibili-Incident-Ops-Key"),
):
    _require_shrimp_bilibili_incident_ops_key(x_key)
    try:
        return complete_corrective_action(
            action_id,
            completion_evidence=payload.completion_evidence,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-incidents")
def shrimp_animation_bilibili_incidents(status: str | None = None, limit: int = 100):
    return list_incidents(status=status,limit=limit)


@app.get("/v1/shrimp-animation/bilibili-incidents/{incident_id}/timeline")
def shrimp_animation_bilibili_incident_timeline(incident_id: UUID):
    return incident_timeline(incident_id)


@app.get("/v1/shrimp-animation/bilibili-recovery-approvals")
def shrimp_animation_bilibili_recovery_approvals(limit: int = 100):
    return list_recovery_approvals(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-notifications")
def shrimp_animation_bilibili_notifications(limit: int = 100):
    return list_notifications(limit=limit)


@app.post("/v1/shrimp-animation/bilibili-incidents/{incident_id}/recovery-request")
def shrimp_animation_bilibili_recovery_request(
    incident_id: UUID,
    payload: ShrimpBilibiliIncidentAction,
    x_shrimp_bilibili_live_acceptance_key: str | None = Header(default=None,alias="X-Shrimp-Bilibili-Live-Acceptance-Key"),
):
    _require_shrimp_bilibili_live_acceptance_key(x_shrimp_bilibili_live_acceptance_key)
    try:
        result=request_recovery(incident_id,actor=payload.actor)
        queue_notification(
            incident_id=incident_id,
            notification_type="RECOVERY_REQUIRED",
            severity="CRITICAL",
            payload={
                "incident_id":str(incident_id),
                "approval_id":result["id"],
                "operations_path":"/animation/operations",
            },
        )
        return result
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-recovery-approvals/{approval_id}/decision")
def shrimp_animation_bilibili_recovery_decision(
    approval_id: UUID,
    payload: ShrimpBilibiliRecoveryDecision,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Bilibili-Recovery-Approval-Key"),
):
    _require_shrimp_bilibili_recovery_approval_key(x_key)
    return decide_recovery(
        approval_id,
        decision=payload.decision,
        reason=payload.reason,
        actor=payload.actor,
    )


@app.post("/v1/shrimp-animation/bilibili-recovery-approvals/{approval_id}/apply")
def shrimp_animation_bilibili_recovery_apply(
    approval_id: UUID,
    payload: ShrimpBilibiliIncidentAction,
    x_key: str | None = Header(default=None,alias="X-Shrimp-Bilibili-Live-Acceptance-Key"),
):
    _require_shrimp_bilibili_live_acceptance_key(x_key)
    with engine.connect() as db:
        fresh=db.execute(text("""
          SELECT EXISTS(
            SELECT 1
            FROM shrimp_bilibili_recovery_approvals r
            JOIN shrimp_bilibili_credential_slots cs
              ON cs.account_id=r.account_id
            WHERE r.id=CAST(:approval_id AS uuid)
              AND cs.last_checked_at IS NOT NULL
              AND cs.last_checked_at >= (
                now() - (:max_age * interval '1 minute')
              )
          )
        """),{
          "approval_id":approval_id,
          "max_age":max(
            1,
            int(settings.shrimp_bilibili_health_max_age_minutes),
          ),
        }).scalar_one()
    if not fresh:
        raise HTTPException(
            status_code=409,
            detail="Recovery health evidence is stale",
        )
    return apply_approved_recovery(
        approval_id,
        actor=payload.actor,
    )


@app.get("/v1/shrimp-animation/bilibili-operations-console")
def shrimp_animation_bilibili_operations_console():
    return operations_console()


@app.get("/v1/shrimp-animation/bilibili-claim-escalations")
def shrimp_animation_bilibili_claim_escalations(
    status: str | None = None,
    limit: int = 100,
):
    return list_claim_escalations(status=status,limit=limit)


@app.get("/v1/shrimp-animation/bilibili-circuit-breakers")
def shrimp_animation_bilibili_circuit_breakers(limit: int = 100):
    return list_circuit_breakers(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-circuit-events")
def shrimp_animation_bilibili_circuit_events(limit: int = 100):
    return list_circuit_events(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-recovery-policy-runs")
def shrimp_animation_bilibili_recovery_policy_runs(limit: int = 100):
    return list_recovery_policy_runs(limit=limit)


@app.post(
    "/v1/shrimp-animation/bilibili-accounts/{account_key}/circuit/evaluate"
)
def shrimp_animation_bilibili_circuit_evaluate(
    account_key: str,
    payload: ShrimpBilibiliCircuitEvaluateAction,
    x_shrimp_bilibili_live_acceptance_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Live-Acceptance-Key",
    ),
):
    _require_shrimp_bilibili_live_acceptance_key(
        x_shrimp_bilibili_live_acceptance_key
    )
    with engine.connect() as db:
        account_id=db.execute(text("""
          SELECT id FROM shrimp_bilibili_accounts
          WHERE account_key=:account_key
        """),{"account_key":account_key}).scalar_one_or_none()
    if account_id is None:
        raise HTTPException(status_code=404,detail="Bilibili account not found")
    return evaluate_account_circuit(
        account_id,
        actor=payload.actor,
    )


@app.get("/v1/shrimp-animation/bilibili-quota-dashboard")
def shrimp_animation_bilibili_quota_dashboard():
    return quota_dashboard()


@app.get("/v1/shrimp-animation/bilibili-stuck-claims")
def shrimp_animation_bilibili_stuck_claims(limit: int = 100):
    return list_stuck_claims(limit=limit)


@app.post(
    "/v1/shrimp-animation/bilibili-stuck-claims/{execution_id}/reconcile"
)
def shrimp_animation_bilibili_stuck_claim_reconcile(
    execution_id: UUID,
    payload: ShrimpBilibiliStuckReconcileAction,
    x_shrimp_bilibili_live_acceptance_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Live-Acceptance-Key",
    ),
):
    _require_shrimp_bilibili_live_acceptance_key(
        x_shrimp_bilibili_live_acceptance_key
    )
    try:
        return reconcile_stuck_claim(
            execution_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-stuck-reconciliations")
def shrimp_animation_bilibili_stuck_reconciliations(limit: int = 100):
    return list_stuck_reconciliations(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-daily-quota-audits")
def shrimp_animation_bilibili_daily_quota_audits(limit: int = 100):
    return list_daily_quota_audits(limit=limit)


@app.get("/v1/shrimp-animation/bilibili-quota/{account_key}")
def shrimp_animation_bilibili_quota(account_key: str):
    try:
        return get_quota_usage(account_key)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-quota-ledger")
def shrimp_animation_bilibili_quota_ledger(
    account_key: str | None = None,
    limit: int = 100,
):
    return list_quota_ledger(account_key=account_key,limit=limit)


@app.get("/v1/shrimp-animation/bilibili-execution-claims")
def shrimp_animation_bilibili_execution_claims(limit: int = 100):
    return list_execution_claims(limit=limit)


@app.post("/v1/shrimp-animation/bilibili-credential-slots/{slot_key}/rotate")
def shrimp_animation_bilibili_credential_rotate(
    slot_key: str,
    payload: ShrimpBilibiliCredentialRotation,
    x_shrimp_publish_key: str | None = Header(default=None,alias="X-Shrimp-Publish-Key"),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return rotate_credential_slot(
            slot_key,
            new_env_prefix=payload.new_env_prefix,
            reason=payload.reason,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.patch("/v1/shrimp-animation/bilibili-credential-slots/{slot_key}/priority")
def shrimp_animation_bilibili_credential_priority(
    slot_key: str,
    payload: ShrimpBilibiliSlotPriorityUpdate,
    x_shrimp_publish_key: str | None = Header(default=None,alias="X-Shrimp-Publish-Key"),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return set_slot_selection_priority(
            slot_key,
            selection_priority=payload.selection_priority,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-account-selection/failover")
def shrimp_animation_bilibili_failover_selection(
    limit: int = 5,
    exclude_account_key: str | None = None,
):
    return select_failover_sacrificial_accounts(
        limit=limit,
        exclude_account_key=exclude_account_key,
    )


@app.get("/v1/shrimp-animation/bilibili-health-monitor/runs")
def shrimp_animation_bilibili_health_monitor_runs(limit: int = 20):
    return list_health_monitor_runs(limit=limit)


@app.post("/v1/shrimp-animation/bilibili-credential-slots", status_code=201)
def shrimp_animation_bilibili_credential_slot_create(
    payload: ShrimpBilibiliCredentialSlotCreate,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return create_credential_slot(
            account_key=payload.account_key,
            slot_key=payload.slot_key,
            env_prefix=payload.env_prefix,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-credential-slots")
def shrimp_animation_bilibili_credential_slots():
    return list_credential_slots()


@app.get("/v1/shrimp-animation/bilibili-credential-slots/{slot_key}")
def shrimp_animation_bilibili_credential_slot(slot_key: str):
    try:
        return get_credential_slot(slot_key)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.patch("/v1/shrimp-animation/bilibili-credential-slots/{slot_key}")
def shrimp_animation_bilibili_credential_slot_update(
    slot_key: str,
    payload: ShrimpBilibiliCredentialSlotUpdate,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return set_credential_slot_status(
            slot_key,
            slot_status=payload.slot_status,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-credential-slots/{slot_key}/health-check")
def shrimp_animation_bilibili_credential_health_check(
    slot_key: str,
    payload: ShrimpBilibiliHealthCheckRequest,
    x_shrimp_bilibili_live_acceptance_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Live-Acceptance-Key",
    ),
):
    _require_shrimp_bilibili_live_acceptance_key(
        x_shrimp_bilibili_live_acceptance_key
    )
    try:
        return run_credential_health_check(
            slot_key,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-credential-slots/{slot_key}/health-checks")
def shrimp_animation_bilibili_credential_health_checks(
    slot_key: str,
    limit: int = 20,
):
    return list_health_checks(slot_key,limit=limit)


@app.get("/v1/shrimp-animation/bilibili-account-selection/healthy")
def shrimp_animation_bilibili_healthy_account_selection():
    selected=select_healthy_sacrificial_account()
    if selected is None:
        raise HTTPException(
            status_code=404,
            detail="No healthy sacrificial Bilibili account is available",
        )
    return selected


@app.post("/v1/shrimp-animation/bilibili-accounts", status_code=201)
def shrimp_animation_bilibili_account_create(
    payload: ShrimpBilibiliAccountCreate,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return create_bilibili_account(
            account_key=payload.account_key,
            display_name=payload.display_name,
            mid=payload.mid,
            tags=payload.tags,
            default_tid=payload.default_tid,
            default_copyright=payload.default_copyright,
            default_description=payload.default_description,
            default_tags=payload.default_tags,
            cover_strategy=payload.cover_strategy,
            daily_publish_limit=payload.daily_publish_limit,
            publish_window_start=payload.publish_window_start,
            publish_window_end=payload.publish_window_end,
            timezone_name=payload.timezone,
            safety_policy=payload.safety_policy,
            actor=payload.actor,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-accounts")
def shrimp_animation_bilibili_accounts(include_inactive: bool = True):
    return list_bilibili_accounts(include_inactive=include_inactive)


@app.get("/v1/shrimp-animation/bilibili-accounts/{account_key}")
def shrimp_animation_bilibili_account(account_key: str):
    try:
        return get_bilibili_account(account_key)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.patch("/v1/shrimp-animation/bilibili-accounts/{account_key}")
def shrimp_animation_bilibili_account_update(
    account_key: str,
    payload: ShrimpBilibiliAccountUpdate,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    changes=payload.model_dump(
        exclude={"actor"},
        exclude_unset=True,
    )
    try:
        return update_bilibili_account(
            account_key,
            changes=changes,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/publish-targets", status_code=201)
def shrimp_animation_publish_target_create(
    payload: ShrimpPublishTargetCreate,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return register_publish_target(
            target_key=payload.target_key,
            platform=payload.platform,
            display_name=payload.display_name,
            account_reference=payload.account_reference,
            metadata_constraints=payload.metadata_constraints,
            actor=payload.actor,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/publish-targets")
def shrimp_animation_publish_targets(active_only: bool = False):
    return list_publish_targets(active_only=active_only)


@app.get("/v1/shrimp-animation/publish-targets/{target_key}")
def shrimp_animation_publish_target(target_key: str):
    try:
        return get_publish_target(target_key)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/jobs/{job_id}/bilibili-reservations",
    status_code=201,
)
def shrimp_animation_bilibili_reservation_create(
    job_id: UUID,
    payload: ShrimpBilibiliReservationCreate,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return create_pre_publish_reservation(
            job_id,
            actor=payload.actor,
            exclude_account_key=payload.exclude_account_key,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-reservations")
def shrimp_animation_bilibili_reservations(
    status: str | None = None,
    limit: int = 50,
):
    return list_reservations(status=status,limit=limit)


@app.get("/v1/shrimp-animation/bilibili-reservations/{reservation_id}")
def shrimp_animation_bilibili_reservation(reservation_id: UUID):
    try:
        return get_reservation(reservation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-reservations/{reservation_id}/release")
def shrimp_animation_bilibili_reservation_release(
    reservation_id: UUID,
    payload: ShrimpBilibiliReservationRelease,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return release_reservation(
            reservation_id,
            reason=payload.reason,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/bilibili-reservations/{reservation_id}/rebind")
def shrimp_animation_bilibili_reservation_rebind(
    reservation_id: UUID,
    payload: ShrimpBilibiliReservationCreate,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return rebind_pre_publish_reservation(
            reservation_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/jobs/{job_id}/routed-publish-plans",
    status_code=201,
)
def shrimp_animation_routed_publish_plan_create(
    job_id: UUID,
    payload: ShrimpBilibiliRoutedPlanCreate,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        reservation=get_reservation(payload.reservation_id)
        if str(reservation["provider_job_id"]) != str(job_id):
            raise RuntimeError("Reservation belongs to a different provider job")
        return create_publish_plan(
            job_id,
            target_key=reservation["target_key"],
            publish_metadata=payload.publish_metadata,
            reservation_id=payload.reservation_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/jobs/{job_id}/publish-plans",
    status_code=201,
)
def shrimp_animation_publish_plan_create(
    job_id: UUID,
    payload: ShrimpPublishPlanCreate,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return create_publish_plan(
            job_id,
            target_key=payload.target_key,
            publish_metadata=payload.publish_metadata,
            reservation_id=payload.reservation_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/jobs/{job_id}/publish-plans")
def shrimp_animation_publish_plans(job_id: UUID):
    return list_publish_plans(job_id)


@app.get("/v1/shrimp-animation/publish-plans/{plan_id}")
def shrimp_animation_publish_plan(plan_id: UUID):
    try:
        return get_publish_plan(plan_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.post("/v1/shrimp-animation/publish-plans/{plan_id}/decision")
def shrimp_animation_publish_plan_decision(
    plan_id: UUID,
    payload: ShrimpPublishAuthorizationDecision,
    x_shrimp_publish_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Key",
    ),
):
    _require_shrimp_publish_key(x_shrimp_publish_key)
    try:
        return decide_publish_authorization(
            plan_id,
            decision=payload.decision,
            reason=payload.reason,
            actor=payload.actor,
            plan_sha256=payload.plan_sha256,
            dry_run_sha256=payload.dry_run_sha256,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc



@app.post(
    "/v1/shrimp-animation/publish-plans/{plan_id}/execution",
    status_code=201,
)
def shrimp_animation_publish_execution_create(
    plan_id: UUID,
    payload: ShrimpPublishExecutionAction,
    x_shrimp_publish_execution_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Execution-Key",
    ),
):
    _require_shrimp_publish_execution_key(
        x_shrimp_publish_execution_key
    )
    try:
        return create_publish_execution(
            plan_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get(
    "/v1/shrimp-animation/publish-plans/{plan_id}/execution"
)
def shrimp_animation_publish_execution_for_plan(plan_id: UUID):
    result=get_publish_execution_for_plan(plan_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Controlled Publisher Execution not found",
        )
    return result


@app.get("/v1/shrimp-animation/jobs/{job_id}/publish-executions")
def shrimp_animation_publish_executions(job_id: UUID):
    return list_publish_executions(job_id)


@app.get("/v1/shrimp-animation/publish-executions/{execution_id}")
def shrimp_animation_publish_execution(execution_id: UUID):
    try:
        return get_publish_execution(execution_id)
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/publish-executions/{execution_id}/upload"
)
def shrimp_animation_publish_execution_upload(
    execution_id: UUID,
    payload: ShrimpPublishExecutionAction,
    x_shrimp_publish_execution_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Execution-Key",
    ),
):
    _require_shrimp_publish_execution_key(
        x_shrimp_publish_execution_key
    )
    try:
        return upload_publish_media(
            execution_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/publish-executions/"
    "{execution_id}/upload/reconcile"
)
def shrimp_animation_publish_execution_upload_reconcile(
    execution_id: UUID,
    payload: ShrimpPublishExecutionAction,
    x_shrimp_publish_execution_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Execution-Key",
    ),
):
    _require_shrimp_publish_execution_key(
        x_shrimp_publish_execution_key
    )
    try:
        return reconcile_publish_upload(
            execution_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/publish-executions/{execution_id}/publish"
)
def shrimp_animation_publish_execution_publish(
    execution_id: UUID,
    payload: ShrimpPublishExecutionAction,
    x_shrimp_publish_execution_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Execution-Key",
    ),
):
    _require_shrimp_publish_execution_key(
        x_shrimp_publish_execution_key
    )
    try:
        return publish_uploaded_media(
            execution_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post(
    "/v1/shrimp-animation/publish-executions/"
    "{execution_id}/publish/reconcile"
)
def shrimp_animation_publish_execution_publish_reconcile(
    execution_id: UUID,
    payload: ShrimpPublishExecutionAction,
    x_shrimp_publish_execution_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Publish-Execution-Key",
    ),
):
    _require_shrimp_publish_execution_key(
        x_shrimp_publish_execution_key
    )
    try:
        return reconcile_published_media(
            execution_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get("/v1/shrimp-animation/bilibili-live-acceptance/readiness")
def shrimp_animation_bilibili_live_acceptance_readiness():
    return _shrimp_bilibili_live_acceptance_readiness()


@app.post(
    "/v1/shrimp-animation/publish-executions/"
    "{execution_id}/bilibili-live-acceptance"
)
def shrimp_animation_bilibili_live_acceptance_run(
    execution_id: UUID,
    payload: ShrimpPublishExecutionAction,
    x_shrimp_bilibili_live_acceptance_key: str | None = Header(
        default=None,
        alias="X-Shrimp-Bilibili-Live-Acceptance-Key",
    ),
):
    _require_shrimp_bilibili_live_acceptance_key(
        x_shrimp_bilibili_live_acceptance_key
    )
    if not settings.shrimp_bilibili_live_acceptance_enabled:
        raise HTTPException(
            status_code=503,
            detail="Shrimp Bilibili live acceptance is disabled",
        )
    try:
        return run_bilibili_live_acceptance(
            execution_id,
            actor=payload.actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.get(
    "/v1/shrimp-animation/publish-executions/"
    "{execution_id}/bilibili-live-acceptance"
)
def shrimp_animation_bilibili_live_acceptance_get(
    execution_id: UUID,
):
    result=get_bilibili_live_acceptance(execution_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Bilibili live acceptance run not found",
        )
    return result


@app.get("/v1/animation/registry")
def animation_registry():
    return list_animation_registry()

@app.post("/v1/production-provider-jobs", status_code=201)
def create_production_provider_job(
    request: ProductionProviderJobCreate,
    x_approval_key: str | None = Header(default=None,alias="X-Approval-Key"),
):
    _require_approval_key(x_approval_key)
    try:
        return create_provider_job(
            request.provider_key,
            request.proposal_id,
            requested_by=request.requested_by,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404,detail=str(exc)) from exc
    except (RuntimeError,ValueError) as exc:
        raise HTTPException(status_code=409,detail=str(exc)) from exc

@app.get("/v1/side-business/providers")
def side_business_providers(limit: int = 100, readiness: str | None = None):
    try:
        return list_side_business_providers(limit=limit,readiness=readiness)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc

@app.get("/v1/side-business/build-queue")
def side_business_build_queue(limit: int = 100):
    return list_side_business_build_queue(limit=limit)

@app.get("/v1/side-business/compositions")
def side_business_compositions(limit: int = 100, status: str | None = None):
    try:
        return list_provider_compositions(limit=limit,status=status)
    except ValueError as exc:
        raise HTTPException(status_code=422,detail=str(exc)) from exc

@app.get("/v1/side-business/composition-runs")
def side_business_composition_runs(limit: int = 30):
    return list_provider_composition_runs(limit=limit)

@app.post("/v1/side-business/compositions/refresh", status_code=202)
def refresh_side_business_compositions(
    x_approval_key: str | None = Header(default=None,alias="X-Approval-Key"),
):
    _require_approval_key(x_approval_key)
    q=Queue("asset-factory",connection=Redis.from_url(settings.redis_url))
    job=q.enqueue(run_provider_composition_cycle,job_timeout=900)
    return {"job_id":job.id,"status":"queued","task":"provider-composition-planner"}

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
