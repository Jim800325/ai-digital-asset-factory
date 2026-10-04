from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_reliability_trend import (
    trend_dashboard,
)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _ser(row: Any) -> dict:
    out=dict(row)
    for key in ("id","review_id","decision_id","scorecard_id","burn_evaluation_id"):
        if out.get(key) is not None:
            out[key]=str(out[key])
    for key in ("generated_at","decided_at","superseded_at","authorized_at"):
        if out.get(key) is not None:
            out[key]=out[key].isoformat()
    return out


def _evidence_snapshot() -> dict:
    dashboard=trend_dashboard()
    score=dashboard.get("latest_global_scorecard")
    burn=dashboard.get("latest_global_burn")
    regressions=dashboard.get("open_regressions") or []
    recurrences=dashboard.get("recurrence_clusters") or []
    recommendations=dashboard.get("recommendations") or []
    with engine.connect() as db:
        observation=db.execute(text("""
          SELECT id,session_status,current_stage,current_quota_percent,
                 refreeze_recommendation,refreeze_reason,
                 latest_evidence_sha256
          FROM shrimp_bilibili_post_unfreeze_observation_sessions
          WHERE session_status IN (
            'ACTIVE','REFREEZE_RECOMMENDED','READY_FOR_ACCEPTANCE'
          )
          ORDER BY created_at DESC
          LIMIT 1
        """)).mappings().one_or_none()
    observation_signal=(
        {
            "session_id":str(observation["id"]),
            "session_status":observation["session_status"],
            "current_stage":int(observation["current_stage"]),
            "current_quota_percent":int(observation["current_quota_percent"]),
            "refreeze_recommendation":observation["refreeze_recommendation"],
            "refreeze_reason":observation["refreeze_reason"],
            "latest_evidence_sha256":observation["latest_evidence_sha256"],
        }
        if observation is not None else None
    )
    with engine.connect() as db:
        reopen=db.execute(text("""
          SELECT e.id,e.certification_id,e.trigger_codes,e.evidence_sha256,
                 e.event_sha256,e.opened_at,c.certification_key,
                 c.baseline_sha256
          FROM shrimp_bilibili_certification_reopen_events e
          JOIN shrimp_bilibili_post_restore_certifications c
            ON c.id=e.certification_id
          WHERE e.event_status='OPEN'
          ORDER BY e.opened_at DESC
          LIMIT 1
        """)).mappings().one_or_none()
    certification_reopen=(
        {
            "event_id":str(reopen["id"]),
            "certification_id":str(reopen["certification_id"]),
            "certification_key":reopen["certification_key"],
            "trigger_codes":list(reopen["trigger_codes"]),
            "evidence_sha256":reopen["evidence_sha256"],
            "event_sha256":reopen["event_sha256"],
            "baseline_sha256":reopen["baseline_sha256"],
            "opened_at":reopen["opened_at"].isoformat(),
        }
        if reopen is not None else None
    )
    with engine.connect() as db:
        lifecycle=db.execute(text("""
          SELECT c.id,c.certification_key,c.certification_status,
                 c.certification_sha256,c.baseline_sha256,
                 c.valid_from,c.renewal_due_at,c.expires_at,
                 c.attestation_sequence,
                 EXISTS(
                   SELECT 1
                   FROM shrimp_bilibili_recertification_candidates r
                   WHERE r.source_certification_id=c.id
                     AND r.candidate_status='PENDING_APPROVAL'
                 ) AS pending_recertification
          FROM shrimp_bilibili_post_restore_certifications c
          WHERE c.certification_status<>'SUPERSEDED'
          ORDER BY c.certified_at DESC,c.id DESC
          LIMIT 1
        """)).mappings().one_or_none()
    certification_lifecycle=(
        {
            "certification_id":str(lifecycle["id"]),
            "certification_key":lifecycle["certification_key"],
            "certification_status":lifecycle["certification_status"],
            "certification_sha256":lifecycle["certification_sha256"],
            "baseline_sha256":lifecycle["baseline_sha256"],
            "valid_from":lifecycle["valid_from"].isoformat(),
            "renewal_due_at":lifecycle["renewal_due_at"].isoformat(),
            "expires_at":lifecycle["expires_at"].isoformat(),
            "attestation_sequence":int(lifecycle["attestation_sequence"]),
            "pending_recertification":bool(
                lifecycle["pending_recertification"]
            ),
        }
        if lifecycle is not None else None
    )
    with engine.connect() as db:
        integrity=db.execute(text("""
          SELECT id,audit_status,issue_codes,audit_snapshot_sha256,
                 audit_sha256,evaluated_at
          FROM shrimp_bilibili_certification_integrity_audits
          ORDER BY evaluated_at DESC,id DESC
          LIMIT 1
        """)).mappings().one_or_none()
        renewal_escalations=db.execute(text("""
          SELECT id,certification_id,escalation_type,severity,
                 evidence_sha256,escalation_sha256,due_at,opened_at
          FROM shrimp_bilibili_renewal_sla_escalations
          WHERE escalation_status='OPEN'
          ORDER BY opened_at DESC,id DESC
          LIMIT 20
        """)).mappings().all()
    integrity_signal=(
        {
            "audit_id":str(integrity["id"]),
            "audit_status":integrity["audit_status"],
            "issue_codes":list(integrity["issue_codes"]),
            "audit_snapshot_sha256":integrity["audit_snapshot_sha256"],
            "audit_sha256":integrity["audit_sha256"],
            "evaluated_at":integrity["evaluated_at"].isoformat(),
        }
        if integrity is not None else None
    )
    renewal_sla_signal=[
        {
            "escalation_id":str(x["id"]),
            "certification_id":str(x["certification_id"]),
            "escalation_type":x["escalation_type"],
            "severity":x["severity"],
            "evidence_sha256":x["evidence_sha256"],
            "escalation_sha256":x["escalation_sha256"],
            "due_at":x["due_at"].isoformat(),
            "opened_at":x["opened_at"].isoformat(),
        }
        for x in renewal_escalations
    ]
    return {
        "scorecard": {
            "id": score.get("id") if score else None,
            "scorecard_sha256": score.get("scorecard_sha256") if score else None,
            "reliability_score": float(score["reliability_score"]) if score else None,
            "reliability_grade": score.get("reliability_grade") if score else None,
            "ack_error_budget_remaining": score.get("ack_error_budget_remaining") if score else None,
            "recovery_error_budget_remaining": score.get("recovery_error_budget_remaining") if score else None,
            "ambiguity_rate_percent": float(score["ambiguity_rate_percent"]) if score else None,
            "circuit_open_count": score.get("circuit_open_count") if score else None,
            "recurring_root_cause_count": score.get("recurring_root_cause_count") if score else None,
        },
        "burn": {
            "id": burn.get("id") if burn else None,
            "evidence_sha256": burn.get("evidence_sha256") if burn else None,
            "burn_status": burn.get("burn_status") if burn else None,
            "ack_short_burn_rate": float(burn["ack_short_burn_rate"]) if burn else None,
            "ack_long_burn_rate": float(burn["ack_long_burn_rate"]) if burn else None,
            "recovery_short_burn_rate": float(burn["recovery_short_burn_rate"]) if burn else None,
            "recovery_long_burn_rate": float(burn["recovery_long_burn_rate"]) if burn else None,
        },
        "open_regressions": [
            {
                "id": x["id"],
                "metric_key": x["metric_key"],
                "severity": x["severity"],
                "evidence_sha256": x["evidence_sha256"],
            }
            for x in regressions
        ],
        "recurrence_clusters": [
            {
                "root_cause_fingerprint": x["root_cause_fingerprint"],
                "recurrence_status": x["recurrence_status"],
                "occurrence_count": x["occurrence_count"],
                "critical_occurrence_count": x["critical_occurrence_count"],
                "evidence_sha256": x["evidence_sha256"],
            }
            for x in recurrences
            if x.get("recurrence_status") in {"WATCH","RECURRING"}
        ],
        "policy_recommendations": [
            {
                "id": x["id"],
                "priority": x["priority"],
                "category": x["category"],
                "recommendation_sha256": x["recommendation_sha256"],
            }
            for x in recommendations
        ],
        "post_unfreeze_observation":observation_signal,
        "post_restore_certification_reopen":certification_reopen,
        "certification_lifecycle":certification_lifecycle,
        "certification_integrity_audit":integrity_signal,
        "renewal_sla_escalations":renewal_sla_signal,
        "observe_only": True,
    }


def _recommend(snapshot: dict) -> tuple[str,str]:
    score=snapshot["scorecard"]
    burn=snapshot["burn"]
    regressions=snapshot["open_regressions"]
    recurrence=snapshot["recurrence_clusters"]
    policies=snapshot["policy_recommendations"]
    observation=snapshot.get("post_unfreeze_observation") or {}
    observation_refreeze=(
        observation.get("refreeze_recommendation")=="REFREEZE_RECOMMENDED"
    )
    certification_reopen=bool(
        snapshot.get("post_restore_certification_reopen")
    )
    lifecycle=snapshot.get("certification_lifecycle") or {}
    recertification_required=(
        lifecycle.get("certification_status")
        in {"EXPIRED","RECERTIFICATION_REQUIRED"}
    )
    integrity=snapshot.get("certification_integrity_audit") or {}
    integrity_failed=integrity.get("audit_status")=="FAIL"
    renewal_sla= snapshot.get("renewal_sla_escalations") or []
    renewal_sla_breached=bool(renewal_sla)

    critical_regression=any(x["severity"]=="CRITICAL" for x in regressions)
    critical_policy=any(x["priority"]=="CRITICAL" for x in policies)
    recurring_critical=any(
        x["recurrence_status"]=="RECURRING"
        and int(x["critical_occurrence_count"] or 0)>=2
        for x in recurrence
    )
    low_score=score["reliability_score"] is not None and score["reliability_score"]<60
    fast_burn=burn["burn_status"] in {"FAST_BURN","EXHAUSTED"}

    if (
        integrity_failed
        or observation_refreeze
        or fast_burn
        or critical_regression
        or critical_policy
        or recurring_critical
        or low_score
    ):
        reasons=[]
        if integrity_failed:
            reasons.append("certification trust chain integrity audit failed")
        if observation_refreeze:
            reasons.append(
                "post-unfreeze observation recommends refreeze"
            )
        if fast_burn: reasons.append("error budget fast burn")
        if critical_regression: reasons.append("critical reliability regression")
        if critical_policy: reasons.append("critical policy recommendation")
        if recurring_critical: reasons.append("repeated critical root cause")
        if low_score: reasons.append("reliability score below 60")
        return "FREEZE_RECOMMENDED",", ".join(reasons)

    caution=(
        certification_reopen
        or recertification_required
        or renewal_sla_breached
        or burn["burn_status"]=="WATCH"
        or bool(regressions)
        or any(x["recurrence_status"]=="RECURRING" for x in recurrence)
        or (score["reliability_score"] is not None and score["reliability_score"]<85)
        or any(x["priority"] in {"HIGH","MEDIUM"} for x in policies)
    )
    if caution:
        if certification_reopen:
            return (
                "CAUTION",
                "post-restore certification reopened; human governance review required",
            )
        if recertification_required:
            return (
                "CAUTION",
                "reliability certification expired; governance re-certification required",
            )
        if renewal_sla_breached:
            return (
                "CAUTION",
                "reliability certification renewal SLA breached; human governance follow-up required",
            )
        return "CAUTION","reliability evidence requires human caution review"

    return "NORMAL","no current burn, regression, or recurrence signal requires escalation"


def generate_governance_review(*,actor:str)->dict:
    snapshot=_evidence_snapshot()
    if snapshot["scorecard"]["id"] is None:
        raise RuntimeError("Reliability scorecard is required before governance review")
    recommendation,reason=_recommend(snapshot)
    evidence_sha=_sha(snapshot)
    review_key="governance-"+evidence_sha[:20]
    with engine.begin() as db:
        existing=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_governance_reviews
          WHERE evidence_sha256=:sha
          ORDER BY generated_at DESC
          LIMIT 1
        """),{"sha":evidence_sha}).mappings().one_or_none()
        if existing is not None:
            return _ser(existing)

        db.execute(text("""
          UPDATE shrimp_bilibili_reliability_governance_reviews
          SET review_status='STALE',superseded_at=now()
          WHERE review_status='PENDING_DECISION'
        """))

        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_governance_reviews(
            review_key,recommendation,recommendation_reason,review_status,
            scorecard_id,burn_evaluation_id,evidence_snapshot,evidence_sha256,
            generated_by)
          VALUES(
            :review_key,:recommendation,:reason,'PENDING_DECISION',
            CAST(:scorecard_id AS uuid),CAST(:burn_id AS uuid),
            CAST(:snapshot AS jsonb),:sha,:actor)
          RETURNING *
        """),{
            "review_key":review_key,
            "recommendation":recommendation,
            "reason":reason,
            "scorecard_id":snapshot["scorecard"]["id"],
            "burn_id":snapshot["burn"]["id"],
            "snapshot":canonical_json(snapshot),
            "sha":evidence_sha,
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def list_governance_reviews(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_governance_reviews
          ORDER BY generated_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def get_governance_review(review_id:UUID)->dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_governance_reviews
          WHERE id=:id
        """),{"id":review_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Reliability governance review not found")
    return _ser(row)


def decide_governance_review(
    review_id:UUID,
    *,
    decision:str,
    reason:str,
    actor:str,
)->dict:
    allowed={
        "NORMAL":{"ACCEPT_NORMAL","REJECT_RECOMMENDATION"},
        "CAUTION":{"ACCEPT_CAUTION","REJECT_RECOMMENDATION"},
        "FREEZE_RECOMMENDED":{"AUTHORIZE_FREEZE_INTENT","REJECT_RECOMMENDATION"},
    }
    with engine.connect() as db:
        preflight=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_governance_reviews
          WHERE id=:id
        """),{"id":review_id}).mappings().one_or_none()
    if preflight is None:
        raise LookupError("Reliability governance review not found")
    if preflight["review_status"]!="PENDING_DECISION":
        raise RuntimeError("Governance review is not pending")
    current_snapshot=_evidence_snapshot()
    current_sha=_sha(current_snapshot)
    if current_sha!=preflight["evidence_sha256"]:
        with engine.begin() as db:
            db.execute(text("""
              UPDATE shrimp_bilibili_reliability_governance_reviews
              SET review_status='STALE',superseded_at=now()
              WHERE id=:id AND review_status='PENDING_DECISION'
            """),{"id":review_id})
        raise RuntimeError("Governance evidence drifted; generate a new review")

    with engine.begin() as db:
        review=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_governance_reviews
          WHERE id=:id FOR UPDATE
        """),{"id":review_id}).mappings().one_or_none()
        if review is None:
            raise LookupError("Reliability governance review not found")
        if review["review_status"]!="PENDING_DECISION":
            raise RuntimeError("Governance review is not pending")
        if decision not in allowed[review["recommendation"]]:
            raise ValueError("Decision is incompatible with governance recommendation")

        decision_material={
            "review_id":str(review_id),
            "decision":decision,
            "reason":reason.strip(),
            "actor":actor.strip(),
            "evidence_sha256":review["evidence_sha256"],
        }
        decision_sha=_sha(decision_material)
        decision_row=db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_governance_decisions(
            review_id,decision,reason,actor,evidence_sha256,decision_sha256)
          VALUES(:review_id,:decision,:reason,:actor,:evidence_sha,:decision_sha)
          RETURNING *
        """),{
            "review_id":review_id,
            "decision":decision,
            "reason":reason.strip(),
            "actor":actor.strip()[:200],
            "evidence_sha":review["evidence_sha256"],
            "decision_sha":decision_sha,
        }).mappings().one()

        intent_type={
            "ACCEPT_NORMAL":"NO_CHANGE",
            "ACCEPT_CAUTION":"CAUTION_CONTROLS",
            "AUTHORIZE_FREEZE_INTENT":"FREEZE_CHANGE_INTENT",
            "REJECT_RECOMMENDATION":"RECOMMENDATION_REJECTED",
        }[decision]
        if intent_type=="FREEZE_CHANGE_INTENT":
            automation_exposure="FREEZE_RECOMMENDED"
        elif intent_type=="CAUTION_CONTROLS":
            automation_exposure="CAUTION_REVIEW"
        else:
            automation_exposure="NO_CHANGE"
        requested_changes={
            "automation_exposure":automation_exposure,
            "publish_quota":"HUMAN_CHANGE_REQUIRED" if intent_type in {"CAUTION_CONTROLS","FREEZE_CHANGE_INTENT"} else "NO_CHANGE",
            "policy_changes":"HUMAN_CHANGE_REQUIRED" if intent_type in {"CAUTION_CONTROLS","FREEZE_CHANGE_INTENT"} else "NO_CHANGE",
            "execution_enabled":False,
            "changes_applied":False,
        }
        intent_material={
            "review_id":str(review_id),
            "decision_id":str(decision_row["id"]),
            "intent_type":intent_type,
            "requested_changes":requested_changes,
        }
        intent_sha=_sha(intent_material)
        intent=db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_policy_intents(
            review_id,decision_id,intent_type,requested_changes,
            intent_status,execution_enabled,changes_applied,
            intent_sha256,authorized_by)
          VALUES(
            :review_id,:decision_id,:intent_type,CAST(:changes AS jsonb),
            'AUTHORIZED_NOT_EXECUTABLE',false,false,:sha,:actor)
          RETURNING *
        """),{
            "review_id":review_id,
            "decision_id":decision_row["id"],
            "intent_type":intent_type,
            "changes":canonical_json(requested_changes),
            "sha":intent_sha,
            "actor":actor.strip()[:200],
        }).mappings().one()
        db.execute(text("""
          UPDATE shrimp_bilibili_reliability_governance_reviews
          SET review_status='DECIDED',decided_at=now()
          WHERE id=:id
        """),{"id":review_id})
    return {
        "review_id":str(review_id),
        "decision":_ser(decision_row),
        "policy_intent":_ser(intent),
        "execution_enabled":False,
        "changes_applied":False,
    }


def list_governance_decisions(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT d.*,r.review_key,r.recommendation
          FROM shrimp_bilibili_reliability_governance_decisions d
          JOIN shrimp_bilibili_reliability_governance_reviews r ON r.id=d.review_id
          ORDER BY d.decided_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def list_policy_intents(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT i.*,r.review_key,r.recommendation
          FROM shrimp_bilibili_reliability_policy_intents i
          JOIN shrimp_bilibili_reliability_governance_reviews r ON r.id=i.review_id
          ORDER BY i.authorized_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def governance_dashboard()->dict:
    reviews=list_governance_reviews(limit=100)
    current=next((x for x in reviews if x["review_status"]=="PENDING_DECISION"),None)
    return {
        "current_review":current,
        "reviews":reviews,
        "decisions":list_governance_decisions(limit=100),
        "policy_intents":list_policy_intents(limit=100),
        "human_gate_required":True,
        "execution_supported":False,
        "changes_applied":False,
        "secrets_redacted":True,
    }
