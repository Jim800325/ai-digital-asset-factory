from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_reliability_governance import (
    _evidence_snapshot as governance_evidence_snapshot,
)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _ser(row: Any) -> dict:
    out=dict(row)
    for key in ("id","intent_id","review_id","decision_id","plan_id","source_plan_id"):
        if out.get(key) is not None:
            out[key]=str(out[key])
    for key in (
        "generated_at","applied_at","stale_at","decided_at",
        "updated_at","created_at",
    ):
        if out.get(key) is not None:
            out[key]=out[key].isoformat()
    return out


def get_policy_control() -> dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_policy_controls
          WHERE control_key='GLOBAL'
        """)).mappings().one()
    return _ser(row)


def _control_snapshot(row: dict) -> dict:
    return {
        "control_key":"GLOBAL",
        "automation_exposure":row["automation_exposure"],
        "quota_multiplier_percent":int(row["quota_multiplier_percent"]),
        "new_reservation_allowed":bool(row["new_reservation_allowed"]),
        "control_version":int(row["control_version"]),
    }


def _proposed_snapshot(current: dict, intent_type: str) -> dict:
    if intent_type=="CAUTION_CONTROLS":
        return {
            **current,
            "automation_exposure":"CAUTION",
            "quota_multiplier_percent":50,
            "new_reservation_allowed":True,
            "control_version":int(current["control_version"])+1,
        }
    if intent_type=="FREEZE_CHANGE_INTENT":
        return {
            **current,
            "automation_exposure":"FROZEN",
            "quota_multiplier_percent":0,
            "new_reservation_allowed":False,
            "control_version":int(current["control_version"])+1,
        }
    raise ValueError("Policy intent does not require a controlled change plan")


def _change_set(current: dict, proposed: dict) -> list[dict]:
    changes=[]
    for key in (
        "automation_exposure",
        "quota_multiplier_percent",
        "new_reservation_allowed",
    ):
        if current[key]!=proposed[key]:
            changes.append({
                "field":key,
                "current":current[key],
                "proposed":proposed[key],
            })
    return changes


def generate_change_plan(intent_id: UUID, *,actor: str) -> dict:
    with engine.begin() as db:
        source=db.execute(text("""
          SELECT i.*,d.decision,r.evidence_sha256 AS governance_evidence_sha256,
                 r.review_status
          FROM shrimp_bilibili_reliability_policy_intents i
          JOIN shrimp_bilibili_reliability_governance_decisions d
            ON d.id=i.decision_id
          JOIN shrimp_bilibili_reliability_governance_reviews r
            ON r.id=i.review_id
          WHERE i.id=:id
          FOR UPDATE OF i
        """),{"id":intent_id}).mappings().one_or_none()
        if source is None:
            raise LookupError("Reliability policy intent not found")
        if source["intent_type"] not in {"CAUTION_CONTROLS","FREEZE_CHANGE_INTENT"}:
            raise ValueError("Policy intent does not authorize a change plan")
        if source["intent_status"]!="AUTHORIZED_NOT_EXECUTABLE":
            raise RuntimeError("Policy intent is not eligible for planning")
        if source["execution_enabled"] or source["changes_applied"]:
            raise RuntimeError("Policy intent invariant violated")

        existing=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_change_plans
          WHERE intent_id=:intent_id
        """),{"intent_id":intent_id}).mappings().one_or_none()
        if existing is not None:
            return _ser(existing)

        db.execute(text("""
          UPDATE shrimp_bilibili_reliability_change_plans
          SET plan_status='STALE',stale_at=now()
          WHERE plan_status='PENDING_APPLY'
        """))

        control=dict(db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_policy_controls
          WHERE control_key='GLOBAL'
          FOR UPDATE
        """)).mappings().one())
        current=_control_snapshot(control)
        proposed=_proposed_snapshot(current,source["intent_type"])
        changes=_change_set(current,proposed)
        if not changes:
            raise RuntimeError("Policy control already matches requested intent")

        dry_run={
            "schema_version":"shrimp-bilibili-reliability-policy-dry-run-v0.1",
            "intent_id":str(intent_id),
            "review_id":str(source["review_id"]),
            "decision_id":str(source["decision_id"]),
            "from":current,
            "to":proposed,
            "changes":changes,
            "runtime_effects":{
                "new_reservations":(
                    "BLOCKED"
                    if not proposed["new_reservation_allowed"]
                    else "ALLOWED"
                ),
                "quota_multiplier_percent":proposed["quota_multiplier_percent"],
                "existing_claims":"UNCHANGED",
                "existing_reservations":"UNCHANGED",
                "circuit_state":"UNCHANGED",
                "publish_authorization_gates":"UNCHANGED",
            },
            "external_provider_write_count":0,
            "credential_access_count":0,
            "changes_applied":False,
        }
        current_sha=_sha(current)
        proposed_sha=_sha(proposed)
        dry_sha=_sha(dry_run)
        plan_material={
            "schema_version":"shrimp-bilibili-reliability-change-plan-v0.1",
            "intent_id":str(intent_id),
            "review_id":str(source["review_id"]),
            "decision_id":str(source["decision_id"]),
            "intent_type":source["intent_type"],
            "governance_evidence_sha256":source["governance_evidence_sha256"],
            "current_control_sha256":current_sha,
            "proposed_control_sha256":proposed_sha,
            "exact_change_set":changes,
            "dry_run_sha256":dry_sha,
        }
        plan_sha=_sha(plan_material)
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_change_plans(
            intent_id,review_id,decision_id,plan_status,
            current_control_snapshot,proposed_control_snapshot,
            exact_change_set,dry_run_diff,current_control_sha256,
            proposed_control_sha256,plan_sha256,dry_run_sha256,
            governance_evidence_sha256,generated_by)
          VALUES(
            :intent_id,:review_id,:decision_id,'PENDING_APPLY',
            CAST(:current AS jsonb),CAST(:proposed AS jsonb),
            CAST(:changes AS jsonb),CAST(:dry_run AS jsonb),
            :current_sha,:proposed_sha,:plan_sha,:dry_sha,
            :governance_sha,:actor)
          RETURNING *
        """),{
            "intent_id":intent_id,
            "review_id":source["review_id"],
            "decision_id":source["decision_id"],
            "current":canonical_json(current),
            "proposed":canonical_json(proposed),
            "changes":canonical_json(changes),
            "dry_run":canonical_json(dry_run),
            "current_sha":current_sha,
            "proposed_sha":proposed_sha,
            "plan_sha":plan_sha,
            "dry_sha":dry_sha,
            "governance_sha":source["governance_evidence_sha256"],
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def list_change_plans(*,limit:int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT p.*,i.intent_type,r.review_key,r.recommendation
          FROM shrimp_bilibili_reliability_change_plans p
          JOIN shrimp_bilibili_reliability_policy_intents i ON i.id=p.intent_id
          JOIN shrimp_bilibili_reliability_governance_reviews r ON r.id=p.review_id
          ORDER BY p.generated_at DESC,p.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def get_change_plan(plan_id: UUID) -> dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT p.*,i.intent_type,r.review_key,r.recommendation
          FROM shrimp_bilibili_reliability_change_plans p
          JOIN shrimp_bilibili_reliability_policy_intents i ON i.id=p.intent_id
          JOIN shrimp_bilibili_reliability_governance_reviews r ON r.id=p.review_id
          WHERE p.id=:id
        """),{"id":plan_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Reliability change plan not found")
    return _ser(row)


def _persist_stale(plan_id: UUID) -> None:
    with engine.begin() as db:
        db.execute(text("""
          UPDATE shrimp_bilibili_reliability_change_plans
          SET plan_status='STALE',stale_at=now()
          WHERE id=:id AND plan_status='PENDING_APPLY'
        """),{"id":plan_id})


def decide_change_plan(
    plan_id: UUID,
    *,
    decision: str,
    reason: str,
    actor: str,
    plan_sha256: str,
    dry_run_sha256: str,
) -> dict:
    plan=get_change_plan(plan_id)
    if plan["plan_status"]!="PENDING_APPLY":
        raise RuntimeError("Reliability change plan is not pending")
    if plan_sha256!=plan["plan_sha256"]:
        raise RuntimeError("Reliability change plan hash mismatch")
    if dry_run_sha256!=plan["dry_run_sha256"]:
        raise RuntimeError("Reliability dry-run hash mismatch")

    current=get_policy_control()
    current_snapshot=_control_snapshot(current)
    if _sha(current_snapshot)!=plan["current_control_sha256"]:
        _persist_stale(plan_id)
        raise RuntimeError("Reliability policy control drifted; generate a new plan")

    governance_sha=_sha(governance_evidence_snapshot())
    if governance_sha!=plan["governance_evidence_sha256"]:
        _persist_stale(plan_id)
        raise RuntimeError("Reliability governance evidence drifted; generate a new review and plan")

    clean_reason=reason.strip()
    if len(clean_reason)<3:
        raise ValueError("decision reason is required")
    if decision not in {"APPLY","REJECT"}:
        raise ValueError("decision must be APPLY or REJECT")

    with engine.begin() as db:
        locked=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_change_plans
          WHERE id=:id FOR UPDATE
        """),{"id":plan_id}).mappings().one()
        if locked["plan_status"]!="PENDING_APPLY":
            raise RuntimeError("Reliability change plan is not pending")

        material={
            "plan_id":str(plan_id),
            "decision":decision,
            "reason":clean_reason,
            "actor":actor.strip(),
            "plan_sha256":plan_sha256,
            "dry_run_sha256":dry_run_sha256,
        }
        decision_sha=_sha(material)
        decision_row=db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_change_apply_decisions(
            plan_id,decision,reason,actor,plan_sha256,dry_run_sha256,
            decision_sha256)
          VALUES(
            :plan_id,:decision,:reason,:actor,:plan_sha,:dry_sha,:decision_sha)
          RETURNING *
        """),{
            "plan_id":plan_id,
            "decision":decision,
            "reason":clean_reason,
            "actor":actor.strip()[:200],
            "plan_sha":plan_sha256,
            "dry_sha":dry_run_sha256,
            "decision_sha":decision_sha,
        }).mappings().one()

        if decision=="REJECT":
            db.execute(text("""
              UPDATE shrimp_bilibili_reliability_change_plans
              SET plan_status='STALE',stale_at=now()
              WHERE id=:id
            """),{"id":plan_id})
            return {
                "plan_id":str(plan_id),
                "decision":_ser(decision_row),
                "plan_status":"STALE",
                "changes_applied":False,
            }

        proposed=dict(locked["proposed_control_snapshot"])
        control=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_policy_controls
          WHERE control_key='GLOBAL' FOR UPDATE
        """)).mappings().one()
        before=_control_snapshot(dict(control))
        if _sha(before)!=locked["current_control_sha256"]:
            raise RuntimeError("Reliability policy control changed during apply")

        updated=db.execute(text("""
          UPDATE shrimp_bilibili_reliability_policy_controls
          SET automation_exposure=:exposure,
              quota_multiplier_percent=:quota_multiplier,
              new_reservation_allowed=:reservation_allowed,
              control_version=:control_version,
              source_plan_id=:plan_id,
              updated_by=:actor,
              updated_at=now()
          WHERE control_key='GLOBAL'
          RETURNING *
        """),{
            "exposure":proposed["automation_exposure"],
            "quota_multiplier":int(proposed["quota_multiplier_percent"]),
            "reservation_allowed":bool(proposed["new_reservation_allowed"]),
            "control_version":int(proposed["control_version"]),
            "plan_id":plan_id,
            "actor":actor.strip()[:200],
        }).mappings().one()
        after=_control_snapshot(dict(updated))
        event_material={
            "plan_id":str(plan_id),
            "event_type":"PLAN_APPLIED",
            "previous_snapshot":before,
            "next_snapshot":after,
        }
        db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_policy_control_events(
            plan_id,event_type,previous_snapshot,next_snapshot,event_sha256,actor)
          VALUES(
            :plan_id,'PLAN_APPLIED',CAST(:previous AS jsonb),
            CAST(:next AS jsonb),:sha,:actor)
        """),{
            "plan_id":plan_id,
            "previous":canonical_json(before),
            "next":canonical_json(after),
            "sha":_sha(event_material),
            "actor":actor.strip()[:200],
        })
        db.execute(text("""
          UPDATE shrimp_bilibili_reliability_change_plans
          SET plan_status='APPLIED',applied_at=now()
          WHERE id=:id
        """),{"id":plan_id})
    return {
        "plan_id":str(plan_id),
        "decision":_ser(decision_row),
        "plan_status":"APPLIED",
        "policy_control":_ser(updated),
        "changes_applied":True,
        "provider_write_count":0,
    }


def list_apply_decisions(*,limit:int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT d.*,p.plan_status
          FROM shrimp_bilibili_reliability_change_apply_decisions d
          JOIN shrimp_bilibili_reliability_change_plans p ON p.id=d.plan_id
          ORDER BY d.decided_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def list_control_events(*,limit:int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_policy_control_events
          ORDER BY created_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def policy_change_dashboard() -> dict:
    plans=list_change_plans(limit=100)
    return {
        "policy_control":get_policy_control(),
        "pending_plan":next((x for x in plans if x["plan_status"]=="PENDING_APPLY"),None),
        "plans":plans,
        "apply_decisions":list_apply_decisions(limit=100),
        "control_events":list_control_events(limit=100),
        "second_human_gate_required":True,
        "dry_run_required":True,
        "provider_writes":False,
        "secrets_redacted":True,
    }


def reservation_policy(db) -> dict:
    row=db.execute(text("""
      SELECT automation_exposure,quota_multiplier_percent,
             new_reservation_allowed,control_version
      FROM shrimp_bilibili_reliability_policy_controls
      WHERE control_key='GLOBAL'
    """)).mappings().one()
    return dict(row)
