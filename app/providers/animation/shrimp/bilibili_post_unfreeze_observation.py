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
from app.providers.animation.shrimp.bilibili_reliability_restore import (
    recovery_evidence_snapshot,
)
from app.providers.animation.shrimp.bilibili_reliability_trend import (
    trend_dashboard,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    generate_certification,
)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _ser(row: Any) -> dict:
    out=dict(row)
    for key in ("id","session_id","restore_plan_id","plan_id","source_plan_id"):
        if out.get(key) is not None:
            out[key]=str(out[key])
    for key in (
        "stage_started_at","observation_started_at","completed_at",
        "created_at","evaluated_at","accepted_at","updated_at",
    ):
        if out.get(key) is not None:
            out[key]=out[key].isoformat()
    return out


def _active_session(db):
    return db.execute(text("""
      SELECT * FROM shrimp_bilibili_post_unfreeze_observation_sessions
      WHERE session_status IN (
        'ACTIVE','REFREEZE_RECOMMENDED','READY_FOR_ACCEPTANCE'
      )
      ORDER BY created_at DESC
      LIMIT 1
    """)).mappings().one_or_none()


def observation_health_snapshot(session: dict) -> dict:
    base=recovery_evidence_snapshot()
    reliability=trend_dashboard()
    score=reliability.get("latest_global_scorecard")
    stage_started=session["stage_started_at"]
    with engine.connect() as db:
        execution_count=int(db.execute(text("""
          SELECT COUNT(*)
          FROM shrimp_animation_publish_executions
          WHERE platform='BILIBILI'
            AND created_at>=:stage_started
            AND (upload_write_count>0 OR publish_write_count>0)
        """),{"stage_started":stage_started}).scalar_one())
        stage_ambiguity_count=int(db.execute(text("""
          SELECT COUNT(*)
          FROM shrimp_animation_publish_executions
          WHERE platform='BILIBILI'
            AND created_at>=:stage_started
            AND (
              execution_status IN ('UPLOAD_UNKNOWN','PUBLISH_UNKNOWN')
              OR upload_outcome='AMBIGUOUS'
              OR publish_outcome='AMBIGUOUS'
            )
        """),{"stage_started":stage_started}).scalar_one())
        stage_failure_count=int(db.execute(text("""
          SELECT COUNT(*)
          FROM shrimp_animation_publish_executions
          WHERE platform='BILIBILI'
            AND created_at>=:stage_started
            AND execution_status IN ('UPLOAD_FAILED','PUBLISH_FAILED')
        """),{"stage_started":stage_started}).scalar_one())

    ambiguity_rate=(
        float(score.get("ambiguity_rate_percent") or 0)
        if score else None
    )
    ambiguity_ok=bool(
        score is not None
        and ambiguity_rate<=float(settings.shrimp_bilibili_ambiguity_target_percent)
    )
    stage_clean=stage_ambiguity_count==0 and stage_failure_count==0
    checks={
        **base["checks"],
        "global_ambiguity_within_target":ambiguity_ok,
        "stage_has_no_ambiguity":stage_ambiguity_count==0,
        "stage_has_no_provider_failures":stage_failure_count==0,
    }
    healthy=all(checks.values())
    return {
        "schema_version":"shrimp-bilibili-post-unfreeze-health-v0.1",
        "healthy_for_ramp":healthy,
        "checks":checks,
        "stage":{
            "session_id":str(session["id"]),
            "stage":int(session["current_stage"]),
            "quota_percent":int(session["current_quota_percent"]),
            "stage_started_at":stage_started.isoformat(),
            "observed_execution_count":execution_count,
            "stage_ambiguity_count":stage_ambiguity_count,
            "stage_failure_count":stage_failure_count,
        },
        "global":{
            "reliability_score":(
                float(score["reliability_score"]) if score else None
            ),
            "ambiguity_rate_percent":ambiguity_rate,
            "ambiguity_target_percent":float(
                settings.shrimp_bilibili_ambiguity_target_percent
            ),
            "burn_status":base["burn"].get("burn_status"),
            "open_regression_ids":base["open_regression_ids"],
            "recurring_root_cause_fingerprints":(
                base["recurring_root_cause_fingerprints"]
            ),
            "unresolved_incident_count":base["unresolved_incident_count"],
            "nonclosed_circuit_count":base["nonclosed_circuit_count"],
            "ambiguous_claim_count":base["ambiguous_claim_count"],
        },
        "provider_writes":False,
    }


def list_observation_sessions(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_unfreeze_observation_sessions
          ORDER BY created_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def get_observation_session(session_id:UUID)->dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_unfreeze_observation_sessions
          WHERE id=:id
        """),{"id":session_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Post-unfreeze observation session not found")
    return _ser(row)


def list_ramp_evaluations(*,limit:int=200)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT e.*,s.restore_plan_id
          FROM shrimp_bilibili_post_unfreeze_ramp_evaluations e
          JOIN shrimp_bilibili_post_unfreeze_observation_sessions s
            ON s.id=e.session_id
          ORDER BY e.evaluated_at DESC,e.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),1000))}).mappings().all()
    return [_ser(x) for x in rows]


def _stage_target(stage:int)->tuple[int|None,int|None,str]:
    if stage==1:
        return 2,50,"ADVANCE_TO_50"
    if stage==2:
        return 3,75,"ADVANCE_TO_75"
    if stage==3:
        return 4,100,"ADVANCE_TO_100"
    return None,None,"READY_FOR_ACCEPTANCE"


def _write_evaluation(
    *,
    session:dict,
    decision:str,
    next_stage:int|None,
    next_quota:int|None,
    elapsed_minutes:int,
    execution_count:int,
    evidence:dict,
    actor:str,
)->dict:
    material={
        "session_id":str(session["id"]),
        "stage_before":int(session["current_stage"]),
        "quota_before":int(session["current_quota_percent"]),
        "decision":decision,
        "recommended_next_stage":next_stage,
        "recommended_quota_percent":next_quota,
        "stage_elapsed_minutes":elapsed_minutes,
        "observed_execution_count":execution_count,
        "health_evidence_sha256":_sha(evidence),
    }
    with engine.begin() as db:
        evaluation_sha=_sha(material)
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_post_unfreeze_ramp_evaluations(
            session_id,stage_before,quota_before,decision,
            recommended_next_stage,recommended_quota_percent,
            stage_elapsed_minutes,observed_execution_count,
            health_evidence_snapshot,health_evidence_sha256,
            evaluation_sha256,evaluated_by)
          VALUES(
            :session_id,:stage_before,:quota_before,:decision,
            :next_stage,:next_quota,:elapsed,:executions,
            CAST(:evidence AS jsonb),:evidence_sha,:evaluation_sha,:actor)
          ON CONFLICT (evaluation_sha256) DO NOTHING
          RETURNING *
        """),{
            "session_id":session["id"],
            "stage_before":session["current_stage"],
            "quota_before":session["current_quota_percent"],
            "decision":decision,
            "next_stage":next_stage,
            "next_quota":next_quota,
            "elapsed":elapsed_minutes,
            "executions":execution_count,
            "evidence":canonical_json(evidence),
            "evidence_sha":material["health_evidence_sha256"],
            "evaluation_sha":evaluation_sha,
            "actor":actor[:200],
        }).mappings().one_or_none()
        if row is None:
            row=db.execute(text("""
              SELECT * FROM shrimp_bilibili_post_unfreeze_ramp_evaluations
              WHERE evaluation_sha256=:sha
            """),{"sha":evaluation_sha}).mappings().one()
    return _ser(row)


def evaluate_observation(*,actor:str)->dict:
    with engine.connect() as db:
        raw=_active_session(db)
    if raw is None:
        raise RuntimeError("No active post-unfreeze observation session")
    session=dict(raw)
    if session["session_status"]!="ACTIVE":
        return {
            "session":_ser(session),
            "decision":"NO_EVALUATION",
            "reason":"Observation session is not ACTIVE",
        }

    evidence=observation_health_snapshot(session)
    now=datetime.now(timezone.utc)
    elapsed=max(
        0,
        int((now-session["stage_started_at"]).total_seconds()//60),
    )
    executions=int(evidence["stage"]["observed_execution_count"])
    min_minutes=max(1,int(settings.shrimp_bilibili_observation_stage_minutes))
    min_executions=max(1,int(settings.shrimp_bilibili_observation_min_executions))

    if not evidence["healthy_for_ramp"]:
        evaluation=_write_evaluation(
            session=session,
            decision="REFREEZE_RECOMMENDED",
            next_stage=None,
            next_quota=None,
            elapsed_minutes=elapsed,
            execution_count=executions,
            evidence=evidence,
            actor=actor,
        )
        failed=[k for k,v in evidence["checks"].items() if not v]
        reason=", ".join(failed) or "observation health degraded"
        with engine.begin() as db:
            db.execute(text("""
              UPDATE shrimp_bilibili_post_unfreeze_observation_sessions
              SET session_status='REFREEZE_RECOMMENDED',
                  refreeze_recommendation='REFREEZE_RECOMMENDED',
                  refreeze_reason=:reason,
                  latest_evidence_sha256=:sha
              WHERE id=:id AND session_status='ACTIVE'
            """),{
                "id":session["id"],
                "reason":reason,
                "sha":evaluation["health_evidence_sha256"],
            })
        return {
            "decision":"REFREEZE_RECOMMENDED",
            "reason":reason,
            "evaluation":evaluation,
            "changes_applied":False,
        }

    if elapsed<min_minutes or executions<min_executions:
        evaluation=_write_evaluation(
            session=session,
            decision="HOLD",
            next_stage=None,
            next_quota=None,
            elapsed_minutes=elapsed,
            execution_count=executions,
            evidence=evidence,
            actor=actor,
        )
        return {
            "decision":"HOLD",
            "required_stage_minutes":min_minutes,
            "required_executions":min_executions,
            "evaluation":evaluation,
            "changes_applied":False,
        }

    next_stage,next_quota,decision=_stage_target(int(session["current_stage"]))
    evaluation=_write_evaluation(
        session=session,
        decision=decision,
        next_stage=next_stage,
        next_quota=next_quota,
        elapsed_minutes=elapsed,
        execution_count=executions,
        evidence=evidence,
        actor=actor,
    )

    if decision=="READY_FOR_ACCEPTANCE":
        acceptance_snapshot={
            "schema_version":"shrimp-bilibili-restore-acceptance-v0.1",
            "session_id":str(session["id"]),
            "stage":session["current_stage"],
            "quota_percent":session["current_quota_percent"],
            "health_evidence_sha256":evaluation["health_evidence_sha256"],
            "health_evidence":evidence,
        }
        with engine.begin() as db:
            db.execute(text("""
              UPDATE shrimp_bilibili_post_unfreeze_observation_sessions
              SET session_status='READY_FOR_ACCEPTANCE',
                  latest_evidence_sha256=:sha
              WHERE id=:id AND session_status='ACTIVE'
            """),{
                "id":session["id"],
                "sha":evaluation["health_evidence_sha256"],
            })
            db.execute(text("""
              INSERT INTO shrimp_bilibili_restore_acceptances(
                session_id,acceptance_status,evidence_snapshot,evidence_sha256)
              VALUES(
                :session_id,'PENDING',CAST(:snapshot AS jsonb),:sha)
              ON CONFLICT (session_id) DO NOTHING
            """),{
                "session_id":session["id"],
                "snapshot":canonical_json(acceptance_snapshot),
                "sha":_sha(acceptance_snapshot),
            })
        return {
            "decision":"READY_FOR_ACCEPTANCE",
            "evaluation":evaluation,
            "changes_applied":False,
        }

    with engine.begin() as db:
        locked=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_unfreeze_observation_sessions
          WHERE id=:id FOR UPDATE
        """),{"id":session["id"]}).mappings().one()
        if (
            locked["session_status"]!="ACTIVE"
            or int(locked["current_stage"])!=int(session["current_stage"])
            or int(locked["current_quota_percent"])
                !=int(session["current_quota_percent"])
        ):
            raise RuntimeError("Observation session changed during evaluation")
        control=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_policy_controls
          WHERE control_key='GLOBAL' FOR UPDATE
        """)).mappings().one()
        before=_control_snapshot(dict(control))
        if (
            before["automation_exposure"]!="OBSERVATION"
            or int(before["quota_multiplier_percent"])
                !=int(locked["current_quota_percent"])
        ):
            db.execute(text("""
              UPDATE shrimp_bilibili_post_unfreeze_observation_sessions
              SET session_status='STALE'
              WHERE id=:id
            """),{"id":session["id"]})
            raise RuntimeError("Observation policy control drifted")

        updated=db.execute(text("""
          UPDATE shrimp_bilibili_reliability_policy_controls
          SET automation_exposure='OBSERVATION',
              quota_multiplier_percent=:quota,
              new_reservation_allowed=true,
              control_version=control_version+1,
              updated_by=:actor,
              updated_at=now()
          WHERE control_key='GLOBAL'
          RETURNING *
        """),{
            "quota":next_quota,
            "actor":actor[:200],
        }).mappings().one()
        after=_control_snapshot(dict(updated))
        event={
            "session_id":str(session["id"]),
            "restore_plan_id":str(session["restore_plan_id"]),
            "event_type":"OBSERVATION_RAMP_APPLIED",
            "previous_snapshot":before,
            "next_snapshot":after,
            "evaluation_sha256":evaluation["evaluation_sha256"],
        }
        db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_policy_control_events(
            plan_id,restore_plan_id,event_type,
            previous_snapshot,next_snapshot,event_sha256,actor)
          VALUES(
            NULL,:restore_plan_id,'OBSERVATION_RAMP_APPLIED',
            CAST(:before AS jsonb),CAST(:after AS jsonb),:sha,:actor)
        """),{
            "restore_plan_id":session["restore_plan_id"],
            "before":canonical_json(before),
            "after":canonical_json(after),
            "sha":_sha(event),
            "actor":actor[:200],
        })
        db.execute(text("""
          UPDATE shrimp_bilibili_post_unfreeze_observation_sessions
          SET current_stage=:stage,
              current_quota_percent=:quota,
              stage_started_at=now(),
              latest_evidence_sha256=:sha
          WHERE id=:id
        """),{
            "id":session["id"],
            "stage":next_stage,
            "quota":next_quota,
            "sha":evaluation["health_evidence_sha256"],
        })

    return {
        "decision":decision,
        "evaluation":evaluation,
        "policy_control":_ser(updated),
        "changes_applied":True,
        "provider_write_count":0,
    }


def list_restore_acceptances(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT a.*,s.restore_plan_id,s.current_stage,s.current_quota_percent
          FROM shrimp_bilibili_restore_acceptances a
          JOIN shrimp_bilibili_post_unfreeze_observation_sessions s
            ON s.id=a.session_id
          ORDER BY a.created_at DESC,a.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def accept_restore(session_id:UUID, *,actor:str)->dict:
    with engine.connect() as db:
        session=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_unfreeze_observation_sessions
          WHERE id=:id
        """),{"id":session_id}).mappings().one_or_none()
        acceptance=db.execute(text("""
          SELECT * FROM shrimp_bilibili_restore_acceptances
          WHERE session_id=:id
        """),{"id":session_id}).mappings().one_or_none()
    if session is None:
        raise LookupError("Observation session not found")
    if acceptance is None:
        raise RuntimeError("Restore acceptance is not prepared")
    if session["session_status"]!="READY_FOR_ACCEPTANCE":
        raise RuntimeError("Observation is not ready for restore acceptance")
    if acceptance["acceptance_status"]!="PENDING":
        raise RuntimeError("Restore acceptance is not pending")

    evidence=observation_health_snapshot(dict(session))
    if not evidence["healthy_for_ramp"]:
        with engine.begin() as db:
            db.execute(text("""
              UPDATE shrimp_bilibili_restore_acceptances
              SET acceptance_status='STALE'
              WHERE id=:id AND acceptance_status='PENDING'
            """),{"id":acceptance["id"]})
            db.execute(text("""
              UPDATE shrimp_bilibili_post_unfreeze_observation_sessions
              SET session_status='REFREEZE_RECOMMENDED',
                  refreeze_recommendation='REFREEZE_RECOMMENDED',
                  refreeze_reason='restore acceptance health degraded',
                  latest_evidence_sha256=:sha
              WHERE id=:session_id
            """),{
                "session_id":session_id,
                "sha":_sha(evidence),
            })
        raise RuntimeError("Restore acceptance health evidence degraded")

    with engine.begin() as db:
        locked=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_unfreeze_observation_sessions
          WHERE id=:id FOR UPDATE
        """),{"id":session_id}).mappings().one()
        control=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_policy_controls
          WHERE control_key='GLOBAL' FOR UPDATE
        """)).mappings().one()
        before=_control_snapshot(dict(control))
        if (
            before["automation_exposure"]!="OBSERVATION"
            or int(before["quota_multiplier_percent"])!=100
            or int(locked["current_stage"])!=4
        ):
            raise RuntimeError("Observation control is not at final 100 percent stage")

        updated=db.execute(text("""
          UPDATE shrimp_bilibili_reliability_policy_controls
          SET automation_exposure='NORMAL',
              quota_multiplier_percent=100,
              new_reservation_allowed=true,
              control_version=control_version+1,
              updated_by=:actor,
              updated_at=now()
          WHERE control_key='GLOBAL'
          RETURNING *
        """),{"actor":actor[:200]}).mappings().one()
        after=_control_snapshot(dict(updated))
        acceptance_material={
            "session_id":str(session_id),
            "acceptance_id":str(acceptance["id"]),
            "actor":actor.strip(),
            "prepared_evidence_sha256":acceptance["evidence_sha256"],
            "current_health_evidence_sha256":_sha(evidence),
            "previous_snapshot":before,
            "next_snapshot":after,
        }
        acceptance_sha=_sha(acceptance_material)
        db.execute(text("""
          UPDATE shrimp_bilibili_restore_acceptances
          SET acceptance_status='ACCEPTED',
              accepted_by=:actor,
              accepted_at=now(),
              acceptance_sha256=:sha
          WHERE id=:id AND acceptance_status='PENDING'
        """),{
            "id":acceptance["id"],
            "actor":actor.strip()[:200],
            "sha":acceptance_sha,
        })
        db.execute(text("""
          UPDATE shrimp_bilibili_post_unfreeze_observation_sessions
          SET session_status='ACCEPTED',completed_at=now(),
              latest_evidence_sha256=:sha
          WHERE id=:session_id
        """),{
            "session_id":session_id,
            "sha":_sha(evidence),
        })
        event={
            "session_id":str(session_id),
            "restore_plan_id":str(session["restore_plan_id"]),
            "event_type":"RESTORE_ACCEPTED",
            "previous_snapshot":before,
            "next_snapshot":after,
            "acceptance_sha256":acceptance_sha,
        }
        db.execute(text("""
          INSERT INTO shrimp_bilibili_reliability_policy_control_events(
            plan_id,restore_plan_id,event_type,
            previous_snapshot,next_snapshot,event_sha256,actor)
          VALUES(
            NULL,:restore_plan_id,'RESTORE_ACCEPTED',
            CAST(:before AS jsonb),CAST(:after AS jsonb),:sha,:actor)
        """),{
            "restore_plan_id":session["restore_plan_id"],
            "before":canonical_json(before),
            "after":canonical_json(after),
            "sha":_sha(event),
            "actor":actor.strip()[:200],
        })
    certification=None
    certification_error=None
    try:
        certification=generate_certification(
            session_id,
            actor=actor.strip()+"-certification",
        )
    except RuntimeError as exc:
        certification_error=str(exc)
    return {
        "session_id":str(session_id),
        "acceptance_status":"ACCEPTED",
        "policy_control":_ser(updated),
        "certification":certification,
        "certification_error":certification_error,
        "provider_write_count":0,
    }


def observation_dashboard()->dict:
    sessions=list_observation_sessions(limit=100)
    current=next((
        x for x in sessions
        if x["session_status"] in {
            "ACTIVE","REFREEZE_RECOMMENDED","READY_FOR_ACCEPTANCE"
        }
    ),None)
    current_health=None
    if current is not None:
        with engine.connect() as db:
            raw=db.execute(text("""
              SELECT * FROM shrimp_bilibili_post_unfreeze_observation_sessions
              WHERE id=:id
            """),{"id":UUID(current["id"])}).mappings().one()
        current_health=observation_health_snapshot(dict(raw))
    return {
        "policy_control":get_policy_control(),
        "current_session":current,
        "current_health":current_health,
        "sessions":sessions,
        "evaluations":list_ramp_evaluations(limit=200),
        "acceptances":list_restore_acceptances(limit=100),
        "stage_minutes":max(1,int(settings.shrimp_bilibili_observation_stage_minutes)),
        "min_executions_per_stage":max(1,int(settings.shrimp_bilibili_observation_min_executions)),
        "ramp":[25,50,75,100],
        "auto_refreeze_execution":False,
        "human_governance_required_for_refreeze":True,
        "restore_acceptance_required":True,
        "provider_writes":False,
        "secrets_redacted":True,
    }
