from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_reliability_policy_change import (
    _control_snapshot,
    get_policy_control,
)
from app.providers.animation.shrimp.bilibili_reliability_trend import trend_dashboard


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _ser(row: Any) -> dict:
    out=dict(row)
    for key in ("id","plan_id","source_plan_id"):
        if out.get(key) is not None:
            out[key]=str(out[key])
    for key in (
        "generated_at","first_approved_at","applied_at",
        "stale_at","decided_at","updated_at",
    ):
        if out.get(key) is not None:
            out[key]=out[key].isoformat()
    return out


def recovery_evidence_snapshot() -> dict:
    reliability=trend_dashboard()
    score=reliability.get("latest_global_scorecard")
    burn=reliability.get("latest_global_burn")
    regressions=reliability.get("open_regressions") or []
    recurrences=reliability.get("recurrence_clusters") or []

    with engine.connect() as db:
        unresolved_incidents=int(db.execute(text("""
          SELECT COUNT(*)
          FROM shrimp_bilibili_incidents
          WHERE incident_status<>'RESOLVED'
        """)).scalar_one())
        nonclosed_circuits=int(db.execute(text("""
          SELECT COUNT(*)
          FROM shrimp_bilibili_account_circuit_breakers
          WHERE circuit_status IN ('OPEN','RECOVERY_PENDING')
        """)).scalar_one())
        ambiguous_claims=int(db.execute(text("""
          SELECT COUNT(*)
          FROM shrimp_bilibili_execution_claims c
          JOIN shrimp_animation_publish_executions e ON e.id=c.execution_id
          WHERE c.claim_status='CLAIMED'
            AND e.execution_status IN (
              'UPLOAD_UNKNOWN','PUBLISH_UNKNOWN'
            )
        """)).scalar_one())

    ack_budget_ok=bool(
        score
        and int(score.get("ack_error_budget_consumed") or 0)
            <= int(score.get("ack_error_budget_allowed") or 0)
    )
    recovery_budget_ok=bool(
        score
        and int(score.get("recovery_error_budget_consumed") or 0)
            <= int(score.get("recovery_error_budget_allowed") or 0)
    )
    burn_ok=bool(burn and burn.get("burn_status")=="HEALTHY")
    regression_ok=not regressions
    recurrence_ok=not any(
        x.get("recurrence_status")=="RECURRING"
        for x in recurrences
    )
    incident_ok=unresolved_incidents==0
    circuit_ok=nonclosed_circuits==0
    ambiguity_ok=ambiguous_claims==0
    score_ok=bool(
        score and float(score.get("reliability_score") or 0)>=85.0
    )
    freshness_hours=max(
        24,
        int(settings.shrimp_bilibili_burn_long_window_hours)+12,
    )
    now=datetime.now(timezone.utc)
    score_generated=(
        datetime.fromisoformat(score["generated_at"])
        if score and score.get("generated_at") else None
    )
    burn_evaluated=(
        datetime.fromisoformat(burn["evaluated_at"])
        if burn and burn.get("evaluated_at") else None
    )
    score_fresh=bool(
        score_generated
        and (now-score_generated).total_seconds()<=freshness_hours*3600
    )
    burn_fresh=bool(
        burn_evaluated
        and (now-burn_evaluated).total_seconds()<=freshness_hours*3600
    )

    checks={
        "scorecard_present":score is not None,
        "scorecard_fresh":score_fresh,
        "burn_evidence_fresh":burn_fresh,
        "reliability_score_at_least_85":score_ok,
        "ack_error_budget_within_limit":ack_budget_ok,
        "recovery_error_budget_within_limit":recovery_budget_ok,
        "burn_healthy":burn_ok,
        "no_open_regressions":regression_ok,
        "no_recurring_root_causes":recurrence_ok,
        "no_unresolved_incidents":incident_ok,
        "all_circuits_closed":circuit_ok,
        "no_ambiguous_claims":ambiguity_ok,
    }
    eligible=all(checks.values())
    return {
        "schema_version":"shrimp-bilibili-safe-unfreeze-evidence-v0.1",
        "eligible_for_restore":eligible,
        "checks":checks,
        "scorecard":{
            "id":score.get("id") if score else None,
            "scorecard_sha256":score.get("scorecard_sha256") if score else None,
            "reliability_score":float(score["reliability_score"]) if score else None,
            "reliability_grade":score.get("reliability_grade") if score else None,
            "ack_error_budget_allowed":score.get("ack_error_budget_allowed") if score else None,
            "ack_error_budget_consumed":score.get("ack_error_budget_consumed") if score else None,
            "recovery_error_budget_allowed":score.get("recovery_error_budget_allowed") if score else None,
            "recovery_error_budget_consumed":score.get("recovery_error_budget_consumed") if score else None,
        },
        "burn":{
            "id":burn.get("id") if burn else None,
            "evidence_sha256":burn.get("evidence_sha256") if burn else None,
            "burn_status":burn.get("burn_status") if burn else None,
        },
        "open_regression_ids":[x["id"] for x in regressions],
        "recurring_root_cause_fingerprints":[
            x["root_cause_fingerprint"]
            for x in recurrences
            if x.get("recurrence_status")=="RECURRING"
        ],
        "unresolved_incident_count":unresolved_incidents,
        "nonclosed_circuit_count":nonclosed_circuits,
        "ambiguous_claim_count":ambiguous_claims,
        "freshness_max_hours":freshness_hours,
        "scorecard_age_hours":(
            round((now-score_generated).total_seconds()/3600,2)
            if score_generated else None
        ),
        "burn_age_hours":(
            round((now-burn_evaluated).total_seconds()/3600,2)
            if burn_evaluated else None
        ),
        "provider_writes":False,
    }


def generate_restore_plan(*,actor:str)->dict:
    control=get_policy_control()
    current=_control_snapshot(control)
    if current["automation_exposure"] not in {"CAUTION","FROZEN"}:
        raise RuntimeError("Safe restore plan requires CAUTION or FROZEN control")

    evidence=recovery_evidence_snapshot()
    if not evidence["eligible_for_restore"]:
        raise RuntimeError("Recovery evidence gate is not satisfied")

    proposed={
        **current,
        "automation_exposure":"NORMAL",
        "quota_multiplier_percent":100,
        "new_reservation_allowed":True,
        "control_version":int(current["control_version"])+1,
    }
    changes=[
        {"field":k,"current":current[k],"proposed":proposed[k]}
        for k in (
            "automation_exposure",
            "quota_multiplier_percent",
            "new_reservation_allowed",
        )
        if current[k]!=proposed[k]
    ]
    evidence_sha=_sha(evidence)
    source_sha=_sha(current)
    proposed_sha=_sha(proposed)
    dry_run={
        "schema_version":"shrimp-bilibili-safe-unfreeze-dry-run-v0.1",
        "from":current,
        "to":proposed,
        "changes":changes,
        "recovery_evidence_sha256":evidence_sha,
        "runtime_effects":{
            "new_reservations":"ALLOWED_AFTER_APPLY",
            "quota_multiplier_percent":100,
            "existing_claims":"UNCHANGED",
            "existing_reservations":"UNCHANGED",
            "circuit_state":"UNCHANGED",
            "publish_authorization_gates":"UNCHANGED",
        },
        "provider_write_count":0,
        "credential_access_count":0,
        "changes_applied":False,
    }
    dry_sha=_sha(dry_run)
    plan_material={
        "schema_version":"shrimp-bilibili-safe-unfreeze-plan-v0.1",
        "source_control_sha256":source_sha,
        "proposed_control_sha256":proposed_sha,
        "recovery_evidence_sha256":evidence_sha,
        "exact_change_set":changes,
        "dry_run_sha256":dry_sha,
    }
    plan_sha=_sha(plan_material)
    restore_key="restore-"+plan_sha[:20]

    with engine.begin() as db:
        existing=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_restore_plans
          WHERE plan_sha256=:sha
          ORDER BY generated_at DESC
          LIMIT 1
        """),{"sha":plan_sha}).mappings().one_or_none()
        if existing is not None:
            return _ser(existing)

        db.execute(text("""
          UPDATE shrimp_bilibili_reliability_restore_plans
          SET plan_status='STALE',stale_at=now()
          WHERE plan_status IN (
            'PENDING_FIRST_APPROVAL','PENDING_SECOND_APPROVAL'
          )
        """))
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_restore_plans(
            restore_key,plan_status,source_control_snapshot,
            proposed_control_snapshot,recovery_evidence_snapshot,
            exact_change_set,dry_run_diff,source_control_sha256,
            proposed_control_sha256,recovery_evidence_sha256,
            plan_sha256,dry_run_sha256,generated_by)
          VALUES(
            :restore_key,'PENDING_FIRST_APPROVAL',
            CAST(:source AS jsonb),CAST(:proposed AS jsonb),
            CAST(:evidence AS jsonb),CAST(:changes AS jsonb),
            CAST(:dry_run AS jsonb),:source_sha,:proposed_sha,
            :evidence_sha,:plan_sha,:dry_sha,:actor)
          RETURNING *
        """),{
            "restore_key":restore_key,
            "source":canonical_json(current),
            "proposed":canonical_json(proposed),
            "evidence":canonical_json(evidence),
            "changes":canonical_json(changes),
            "dry_run":canonical_json(dry_run),
            "source_sha":source_sha,
            "proposed_sha":proposed_sha,
            "evidence_sha":evidence_sha,
            "plan_sha":plan_sha,
            "dry_sha":dry_sha,
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def list_restore_plans(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_restore_plans
          ORDER BY generated_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def get_restore_plan(plan_id:UUID)->dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_restore_plans
          WHERE id=:id
        """),{"id":plan_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Reliability restore plan not found")
    return _ser(row)


def _stale(plan_id:UUID)->None:
    with engine.begin() as db:
        db.execute(text("""
          UPDATE shrimp_bilibili_reliability_restore_plans
          SET plan_status='STALE',stale_at=now()
          WHERE id=:id
            AND plan_status IN (
              'PENDING_FIRST_APPROVAL','PENDING_SECOND_APPROVAL'
            )
        """),{"id":plan_id})


def _preflight(plan:dict, plan_sha256:str, dry_run_sha256:str)->None:
    if plan_sha256!=plan["plan_sha256"]:
        raise RuntimeError("Restore plan hash mismatch")
    if dry_run_sha256!=plan["dry_run_sha256"]:
        raise RuntimeError("Restore dry-run hash mismatch")
    current=_control_snapshot(get_policy_control())
    if _sha(current)!=plan["source_control_sha256"]:
        _stale(UUID(plan["id"]))
        raise RuntimeError("Reliability policy control drifted; generate a new restore plan")
    evidence=recovery_evidence_snapshot()
    if not evidence["eligible_for_restore"]:
        _stale(UUID(plan["id"]))
        raise RuntimeError("Recovery evidence gate is no longer satisfied")
    if _sha(evidence)!=plan["recovery_evidence_sha256"]:
        _stale(UUID(plan["id"]))
        raise RuntimeError("Recovery evidence drifted; generate a new restore plan")


def first_restore_approval(
    plan_id:UUID,
    *,
    decision:str,
    reason:str,
    actor:str,
    plan_sha256:str,
    dry_run_sha256:str,
)->dict:
    plan=get_restore_plan(plan_id)
    if plan["plan_status"]!="PENDING_FIRST_APPROVAL":
        raise RuntimeError("Restore plan is not awaiting first approval")
    _preflight(plan,plan_sha256,dry_run_sha256)
    if decision not in {"APPROVE","REJECT"}:
        raise ValueError("decision must be APPROVE or REJECT")
    clean_reason=reason.strip()
    if len(clean_reason)<3:
        raise ValueError("approval reason is required")

    material={
        "plan_id":str(plan_id),"stage":"FIRST_APPROVAL",
        "decision":decision,"reason":clean_reason,"actor":actor.strip(),
        "plan_sha256":plan_sha256,"dry_run_sha256":dry_run_sha256,
        "recovery_evidence_sha256":plan["recovery_evidence_sha256"],
    }
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_restore_approvals(
            plan_id,approval_stage,decision,reason,actor,
            plan_sha256,dry_run_sha256,recovery_evidence_sha256,
            approval_sha256)
          VALUES(
            :plan_id,'FIRST_APPROVAL',:decision,:reason,:actor,
            :plan_sha,:dry_sha,:evidence_sha,:approval_sha)
          RETURNING *
        """),{
            "plan_id":plan_id,"decision":decision,"reason":clean_reason,
            "actor":actor.strip()[:200],"plan_sha":plan_sha256,
            "dry_sha":dry_run_sha256,
            "evidence_sha":plan["recovery_evidence_sha256"],
            "approval_sha":_sha(material),
        }).mappings().one()
        next_status=(
            "PENDING_SECOND_APPROVAL" if decision=="APPROVE" else "REJECTED"
        )
        db.execute(text("""
          UPDATE shrimp_bilibili_reliability_restore_plans
          SET plan_status=:status,
              first_approved_at=CASE
                WHEN :status='PENDING_SECOND_APPROVAL' THEN now()
                ELSE first_approved_at
              END
          WHERE id=:id
        """),{"id":plan_id,"status":next_status})
    return {"approval":_ser(row),"plan_status":next_status,"changes_applied":False}


def second_restore_apply(
    plan_id:UUID,
    *,
    decision:str,
    reason:str,
    actor:str,
    plan_sha256:str,
    dry_run_sha256:str,
)->dict:
    plan=get_restore_plan(plan_id)
    if plan["plan_status"]!="PENDING_SECOND_APPROVAL":
        raise RuntimeError("Restore plan is not awaiting second approval")
    _preflight(plan,plan_sha256,dry_run_sha256)
    if decision not in {"APPROVE","REJECT"}:
        raise ValueError("decision must be APPROVE or REJECT")
    clean_reason=reason.strip()
    if len(clean_reason)<3:
        raise ValueError("approval reason is required")

    with engine.connect() as db:
        first=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_restore_approvals
          WHERE plan_id=:plan_id AND approval_stage='FIRST_APPROVAL'
        """),{"plan_id":plan_id}).mappings().one()
    if first["decision"]!="APPROVE":
        raise RuntimeError("First restore approval is not APPROVE")
    if first["actor"].strip().casefold()==actor.strip().casefold():
        raise RuntimeError("Second restore approver must be a different actor")

    material={
        "plan_id":str(plan_id),"stage":"SECOND_APPLY",
        "decision":decision,"reason":clean_reason,"actor":actor.strip(),
        "plan_sha256":plan_sha256,"dry_run_sha256":dry_run_sha256,
        "recovery_evidence_sha256":plan["recovery_evidence_sha256"],
    }
    with engine.begin() as db:
        locked=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_restore_plans
          WHERE id=:id FOR UPDATE
        """),{"id":plan_id}).mappings().one()
        if locked["plan_status"]!="PENDING_SECOND_APPROVAL":
            raise RuntimeError("Restore plan changed during second approval")

        approval=db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_restore_approvals(
            plan_id,approval_stage,decision,reason,actor,
            plan_sha256,dry_run_sha256,recovery_evidence_sha256,
            approval_sha256)
          VALUES(
            :plan_id,'SECOND_APPLY',:decision,:reason,:actor,
            :plan_sha,:dry_sha,:evidence_sha,:approval_sha)
          RETURNING *
        """),{
            "plan_id":plan_id,"decision":decision,"reason":clean_reason,
            "actor":actor.strip()[:200],"plan_sha":plan_sha256,
            "dry_sha":dry_run_sha256,
            "evidence_sha":plan["recovery_evidence_sha256"],
            "approval_sha":_sha(material),
        }).mappings().one()

        if decision=="REJECT":
            db.execute(text("""
              UPDATE shrimp_bilibili_reliability_restore_plans
              SET plan_status='REJECTED'
              WHERE id=:id
            """),{"id":plan_id})
            return {
                "approval":_ser(approval),
                "plan_status":"REJECTED",
                "changes_applied":False,
            }

        control=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_policy_controls
          WHERE control_key='GLOBAL' FOR UPDATE
        """)).mappings().one()
        before=_control_snapshot(dict(control))
        if _sha(before)!=locked["source_control_sha256"]:
            raise RuntimeError("Policy control changed during restore apply")
        proposed=dict(locked["proposed_control_snapshot"])
        updated=db.execute(text("""
          UPDATE shrimp_bilibili_reliability_policy_controls
          SET automation_exposure='NORMAL',
              quota_multiplier_percent=100,
              new_reservation_allowed=true,
              control_version=:version,
              source_plan_id=NULL,
              updated_by=:actor,
              updated_at=now()
          WHERE control_key='GLOBAL'
          RETURNING *
        """),{
            "version":int(proposed["control_version"]),
            "actor":actor.strip()[:200],
        }).mappings().one()
        after=_control_snapshot(dict(updated))
        event={
            "restore_plan_id":str(plan_id),
            "event_type":"SAFE_UNFREEZE_APPLIED",
            "previous_snapshot":before,
            "next_snapshot":after,
        }
        db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_policy_control_events(
            plan_id,restore_plan_id,event_type,
            previous_snapshot,next_snapshot,event_sha256,actor)
          VALUES(
            NULL,:restore_plan_id,'SAFE_UNFREEZE_APPLIED',
            CAST(:before AS jsonb),CAST(:after AS jsonb),:sha,:actor)
        """),{
            "restore_plan_id":plan_id,
            "before":canonical_json(before),"after":canonical_json(after),
            "sha":_sha(event),"actor":actor.strip()[:200],
        })
        db.execute(text("""
          UPDATE shrimp_bilibili_reliability_restore_plans
          SET plan_status='APPLIED',applied_at=now()
          WHERE id=:id
        """),{"id":plan_id})
    return {
        "approval":_ser(approval),
        "plan_status":"APPLIED",
        "policy_control":_ser(updated),
        "changes_applied":True,
        "provider_write_count":0,
    }


def list_restore_approvals(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT a.*,p.restore_key,p.plan_status
          FROM shrimp_bilibili_reliability_restore_approvals a
          JOIN shrimp_bilibili_reliability_restore_plans p ON p.id=a.plan_id
          ORDER BY a.decided_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def safe_unfreeze_dashboard()->dict:
    plans=list_restore_plans(limit=100)
    evidence=recovery_evidence_snapshot()
    return {
        "policy_control":get_policy_control(),
        "recovery_evidence":evidence,
        "current_plan":next((
            x for x in plans
            if x["plan_status"] in {
                "PENDING_FIRST_APPROVAL","PENDING_SECOND_APPROVAL"
            }
        ),None),
        "plans":plans,
        "approvals":list_restore_approvals(limit=100),
        "two_person_restore_required":True,
        "eligible_for_restore":evidence["eligible_for_restore"],
        "provider_writes":False,
        "secrets_redacted":True,
    }
