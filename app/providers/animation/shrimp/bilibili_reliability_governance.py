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
        "observe_only": True,
    }


def _recommend(snapshot: dict) -> tuple[str,str]:
    score=snapshot["scorecard"]
    burn=snapshot["burn"]
    regressions=snapshot["open_regressions"]
    recurrence=snapshot["recurrence_clusters"]
    policies=snapshot["policy_recommendations"]

    critical_regression=any(x["severity"]=="CRITICAL" for x in regressions)
    critical_policy=any(x["priority"]=="CRITICAL" for x in policies)
    recurring_critical=any(
        x["recurrence_status"]=="RECURRING"
        and int(x["critical_occurrence_count"] or 0)>=2
        for x in recurrence
    )
    low_score=score["reliability_score"] is not None and score["reliability_score"]<60
    fast_burn=burn["burn_status"] in {"FAST_BURN","EXHAUSTED"}

    if fast_burn or critical_regression or critical_policy or recurring_critical or low_score:
        reasons=[]
        if fast_burn: reasons.append("error budget fast burn")
        if critical_regression: reasons.append("critical reliability regression")
        if critical_policy: reasons.append("critical policy recommendation")
        if recurring_critical: reasons.append("repeated critical root cause")
        if low_score: reasons.append("reliability score below 60")
        return "FREEZE_RECOMMENDED",", ".join(reasons)

    caution=(
        burn["burn_status"]=="WATCH"
        or bool(regressions)
        or any(x["recurrence_status"]=="RECURRING" for x in recurrence)
        or (score["reliability_score"] is not None and score["reliability_score"]<85)
        or any(x["priority"] in {"HIGH","MEDIUM"} for x in policies)
    )
    if caution:
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
