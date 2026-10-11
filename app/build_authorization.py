from __future__ import annotations

import hashlib
import json
import secrets
from uuid import UUID

from sqlalchemy import text

from app.build_proposals import _current_source_state, _is_build_ready, _proposal_payload
from app.config import settings
from app.db import engine
from app.sandbox_policy import validate_execution_policy


def _policy_payload(executor_kind: str) -> dict:
    executor=executor_kind.upper().strip()
    image=(
        settings.openhands_cli_image
        if executor=="OPENHANDS"
        else settings.sandbox_image
    )
    payload=validate_execution_policy(
        executor_kind=executor,
        sandbox_image=image,
        require_execution_enabled=False,
    )
    payload["authorization_contract"]="SBW4_BUILD_AUTHORIZATION_V1"
    if executor=="OPENHANDS":
        payload["openhands"]={
            "runtime":settings.openhands_runtime.strip().lower(),
            "cli_version":settings.openhands_cli_version.strip(),
            "cli_image":settings.openhands_cli_image.strip(),
            "gateway_mode":settings.openhands_gateway_mode.strip().upper(),
            "model":settings.openhands_model.strip(),
            "allowed_models":settings.openhands_allowed_model_list,
            "max_requests":int(settings.openhands_max_requests),
            "max_prompt_tokens_per_request":int(
                settings.openhands_max_prompt_tokens_per_request
            ),
            "max_completion_tokens_per_request":int(
                settings.openhands_max_completion_tokens_per_request
            ),
            "max_total_tokens":int(settings.openhands_max_total_tokens),
            "max_cost_per_request_usd":float(
                settings.openhands_max_cost_per_request_usd
            ),
            "max_cost_usd":float(settings.openhands_max_cost_usd),
        }
    return payload


def policy_fingerprint(policy: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            policy,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",",":"),
        ).encode("utf-8")
    ).hexdigest()


def current_authorization_policy(executor_kind: str) -> dict:
    policy=_policy_payload(executor_kind)
    return {
        "policy":policy,
        "policy_fingerprint":policy_fingerprint(policy),
    }


def _proposal_state(db, proposal_id: UUID) -> dict | None:
    row=db.execute(text("""
      SELECT id,opportunity_id,revision,proposal_status,source_fingerprint,
             requires_human_approval,execution_enabled,updated_at
      FROM build_proposals
      WHERE id=:id
    """),{"id":proposal_id}).mappings().one_or_none()
    return dict(row) if row else None


def _approval_decision(db, proposal_id: UUID, revision: int) -> dict | None:
    row=db.execute(text("""
      SELECT id,decision,reason,actor,decided_at
      FROM build_proposal_decisions
      WHERE proposal_id=:proposal_id
        AND proposal_revision=:revision
        AND decision='APPROVE'
      ORDER BY decided_at DESC,id DESC
      LIMIT 1
    """),{"proposal_id":proposal_id,"revision":revision}).mappings().one_or_none()
    return dict(row) if row else None


def _source_is_current(db, proposal: dict) -> tuple[bool,str | None]:
    opportunity,report,validation=_current_source_state(
        db,
        proposal["opportunity_id"],
    )
    if not _is_build_ready(opportunity,report,validation):
        return False,"BUILD_READY_NOT_CURRENT"
    current=_proposal_payload(
        dict(opportunity),
        dict(report),
        dict(validation),
    )
    if current["source_fingerprint"]!=proposal["source_fingerprint"]:
        return False,"PROPOSAL_SOURCE_STALE"
    return True,None


def build_authorization_readiness(
    proposal_id: UUID,
    *,
    executor_kind: str = "OPENHANDS",
) -> dict:
    executor=executor_kind.upper().strip()
    blockers=[]
    key_configured=bool(settings.build_authorization_key.strip())
    if not settings.build_authorization_enabled:
        blockers.append("BUILD_AUTHORIZATION_DISABLED")
    if not key_configured:
        blockers.append("BUILD_AUTHORIZATION_KEY")
    if executor not in {"OPENHANDS","ACCEPTANCE"}:
        blockers.append("UNSUPPORTED_EXECUTOR")

    proposal=None
    approval=None
    existing=None
    source_current=False
    source_reason=None
    with engine.connect() as db:
        proposal=_proposal_state(db,proposal_id)
        if proposal is None:
            blockers.append("PROPOSAL_NOT_FOUND")
        else:
            if proposal["proposal_status"]!="APPROVED":
                blockers.append("PROPOSAL_NOT_APPROVED")
            if not proposal["requires_human_approval"]:
                blockers.append("HUMAN_APPROVAL_INVARIANT")
            if proposal["execution_enabled"]:
                blockers.append("PRODUCTION_EXECUTION_FLAG_ENABLED")
            approval=_approval_decision(
                db,
                proposal_id,
                int(proposal["revision"]),
            )
            if approval is None:
                blockers.append("APPROVAL_DECISION_MISSING")
            source_current,source_reason=_source_is_current(db,proposal)
            if not source_current and source_reason:
                blockers.append(source_reason)
            row=db.execute(text("""
              SELECT id,authorization_status,executor_kind,policy_fingerprint,
                     actor,reason,created_at,authorized_at,rejected_at,consumed_at
              FROM sandbox_build_authorizations
              WHERE proposal_id=:proposal_id
                AND proposal_revision=:revision
              ORDER BY created_at DESC,id DESC
              LIMIT 1
            """),{
                "proposal_id":proposal_id,
                "revision":proposal["revision"],
            }).mappings().one_or_none()
            existing=dict(row) if row else None
            outstanding=db.execute(text("""
              SELECT id
              FROM sandbox_build_authorizations
              WHERE proposal_id=:proposal_id
                AND proposal_revision=:revision
                AND authorization_status='AUTHORIZED'
              ORDER BY created_at DESC,id DESC
              LIMIT 1
            """),{
                "proposal_id":proposal_id,
                "revision":proposal["revision"],
            }).scalar_one_or_none()
            if outstanding is not None:
                blockers.append("AUTHORIZATION_OUTSTANDING")

    policy_data=None
    policy_error=None
    if executor in {"OPENHANDS","ACCEPTANCE"}:
        try:
            policy_data=current_authorization_policy(executor)
        except RuntimeError as exc:
            policy_error=str(exc)
            blockers.append("EXECUTOR_POLICY_NOT_READY")

    return {
        "status":"READY" if not blockers else "BLOCKED",
        "mode":"HUMAN_BUILD_AUTHORIZATION",
        "authorization_enabled":bool(settings.build_authorization_enabled),
        "key_configured":key_configured,
        "proposal_id":str(proposal_id),
        "proposal_status":proposal["proposal_status"] if proposal else None,
        "proposal_revision":int(proposal["revision"]) if proposal else None,
        "source_current":source_current,
        "executor_kind":executor,
        "approval_decision_id":(
            str(approval["id"]) if approval else None
        ),
        "existing_authorization":(
            {
                **existing,
                "id":str(existing["id"]),
            }
            if existing else None
        ),
        "blockers":blockers,
        "policy":policy_data,
        "policy_error":policy_error,
        "safety":{
            "creates_sandbox_request":False,
            "executes_build":False,
            "sandbox_execution_enabled":bool(settings.sandbox_execution_enabled),
            "production_execution_enabled":False,
            "production_provider_writes":False,
            "automatic_publish":False,
            "external_side_effects":"DENY",
        },
    }


def _require_authorization_key(provided: str | None) -> None:
    expected=settings.build_authorization_key.strip()
    if not settings.build_authorization_enabled:
        raise RuntimeError("Build authorization is disabled")
    if not expected:
        raise RuntimeError("Build authorization key is not configured")
    if not provided or not secrets.compare_digest(provided,expected):
        raise PermissionError("Invalid build authorization key")


def decide_build_authorization(
    proposal_id: UUID,
    *,
    decision: str,
    reason: str,
    actor: str,
    executor_kind: str,
    provided_key: str | None,
) -> dict:
    _require_authorization_key(provided_key)

    normalized=decision.upper().strip()
    if normalized not in {"AUTHORIZE","REJECT"}:
        raise ValueError("decision must be AUTHORIZE or REJECT")
    clean_reason=(reason or "").strip()
    if len(clean_reason)<3:
        raise ValueError("reason must contain at least 3 characters")
    clean_actor=(actor or "human-build-authorizer").strip() or "human-build-authorizer"
    executor=executor_kind.upper().strip()
    if executor not in {"OPENHANDS","ACCEPTANCE"}:
        raise ValueError("executor_kind must be OPENHANDS or ACCEPTANCE")

    policy_data=current_authorization_policy(executor)
    status="AUTHORIZED" if normalized=="AUTHORIZE" else "REJECTED"

    with engine.begin() as db:
        proposal=db.execute(text("""
          SELECT id,opportunity_id,revision,proposal_status,source_fingerprint,
                 requires_human_approval,execution_enabled
          FROM build_proposals
          WHERE id=:id
          FOR UPDATE
        """),{"id":proposal_id}).mappings().one_or_none()
        if proposal is None:
            raise LookupError("Build proposal not found")
        proposal=dict(proposal)
        if proposal["proposal_status"]!="APPROVED":
            raise RuntimeError("Build proposal must be APPROVED before build authorization")
        if not proposal["requires_human_approval"]:
            raise RuntimeError("Human approval invariant failed")
        if proposal["execution_enabled"]:
            raise RuntimeError("Production execution flag must remain disabled")

        approval=_approval_decision(
            db,
            proposal_id,
            int(proposal["revision"]),
        )
        if approval is None:
            raise RuntimeError("Current proposal revision has no APPROVE decision")

        source_current,source_reason=_source_is_current(db,proposal)
        if not source_current:
            raise RuntimeError(
                "Build authorization denied: "+str(source_reason or "SOURCE_NOT_CURRENT")
            )

        existing=db.execute(text("""
          SELECT id,authorization_status
          FROM sandbox_build_authorizations
          WHERE proposal_id=:proposal_id
            AND proposal_revision=:revision
            AND authorization_status='AUTHORIZED'
          ORDER BY created_at DESC,id DESC
          LIMIT 1
          FOR UPDATE
        """),{
            "proposal_id":proposal_id,
            "revision":proposal["revision"],
        }).mappings().one_or_none()
        if existing is not None:
            raise RuntimeError(
                "An unconsumed build authorization already exists for this proposal revision"
            )

        authorization_id=db.execute(text("""
          INSERT INTO sandbox_build_authorizations(
            proposal_id,proposal_revision,approval_decision_id,source_fingerprint,
            executor_kind,authorization_status,policy_snapshot,policy_fingerprint,
            reason,actor,authorized_at,rejected_at)
          VALUES(
            :proposal_id,:revision,:approval_decision_id,:source_fingerprint,
            :executor_kind,:status,CAST(:policy AS jsonb),:policy_fingerprint,
            :reason,:actor,
            CASE WHEN :status='AUTHORIZED' THEN now() ELSE NULL END,
            CASE WHEN :status='REJECTED' THEN now() ELSE NULL END)
          RETURNING id
        """),{
            "proposal_id":proposal_id,
            "revision":proposal["revision"],
            "approval_decision_id":approval["id"],
            "source_fingerprint":proposal["source_fingerprint"],
            "executor_kind":executor,
            "status":status,
            "policy":json.dumps(policy_data["policy"],ensure_ascii=False),
            "policy_fingerprint":policy_data["policy_fingerprint"],
            "reason":clean_reason[:4000],
            "actor":clean_actor[:200],
        }).scalar_one()

    return {
        "authorization_id":str(authorization_id),
        "proposal_id":str(proposal_id),
        "proposal_revision":int(proposal["revision"]),
        "approval_decision_id":str(approval["id"]),
        "authorization_status":status,
        "executor_kind":executor,
        "policy_fingerprint":policy_data["policy_fingerprint"],
        "creates_sandbox_request":False,
        "executes_build":False,
        "sandbox_execution_enabled":bool(settings.sandbox_execution_enabled),
        "production_execution_enabled":False,
        "external_side_effects":"DENY",
    }


def get_build_authorization(proposal_id: UUID) -> dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT sba.id,sba.proposal_id,sba.proposal_revision,
                 sba.approval_decision_id,sba.source_fingerprint,
                 sba.executor_kind,sba.authorization_status,
                 sba.policy_snapshot,sba.policy_fingerprint,
                 sba.reason,sba.actor,sba.created_at,sba.authorized_at,
                 sba.rejected_at,sba.consumed_at,
                 sbr.id AS sandbox_request_id,
                 sbr.request_status AS sandbox_request_status
          FROM sandbox_build_authorizations sba
          LEFT JOIN sandbox_build_requests sbr
            ON sbr.authorization_id=sba.id
          WHERE sba.proposal_id=:proposal_id
          ORDER BY sba.proposal_revision DESC,sba.created_at DESC,sba.id DESC
          LIMIT 1
        """),{"proposal_id":proposal_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Build authorization not found")
    payload=dict(row)
    for key in (
        "id","proposal_id","approval_decision_id","sandbox_request_id"
    ):
        if payload.get(key) is not None:
            payload[key]=str(payload[key])
    return payload
