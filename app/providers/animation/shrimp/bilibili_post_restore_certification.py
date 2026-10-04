from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_reliability_policy_change import (
    get_policy_control,
)
from app.providers.animation.shrimp.bilibili_reliability_trend import (
    trend_dashboard,
)
from app.providers.animation.shrimp.bilibili_incidents import (
    queue_notification,
)


def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _ser(row: Any) -> dict:
    out=dict(row)
    for key in (
        "id","observation_session_id","restore_acceptance_id",
        "certification_id","evaluation_id","previous_certification_id",
    ):
        if out.get(key) is not None:
            out[key]=str(out[key])
    for key in (
        "certified_at","superseded_at","evaluated_at",
        "opened_at","closed_at","valid_from","expires_at","renewal_due_at",
    ):
        if out.get(key) is not None:
            out[key]=out[key].isoformat()
    return out


def _certification_validity(now: datetime | None = None) -> tuple[datetime,datetime,datetime]:
    current=now or datetime.now(timezone.utc)
    valid_days=max(2,int(settings.shrimp_bilibili_certification_valid_days))
    warning_days=max(
        1,
        min(
            valid_days-1,
            int(settings.shrimp_bilibili_certification_expiry_warning_days),
        ),
    )
    expires=current+timedelta(days=valid_days)
    renewal_due=expires-timedelta(days=warning_days)
    return current,renewal_due,expires


def _append_attestation(
    db,
    *,
    certification_id,
    attestation_type:str,
    attestation_sequence:int,
    attestation_status:str,
    evidence_snapshot:dict,
    actor:str,
    previous_attestation_id=None,
) -> dict:
    evidence_sha=_sha(evidence_snapshot)
    material={
        "certification_id":str(certification_id),
        "previous_attestation_id":(
            str(previous_attestation_id) if previous_attestation_id else None
        ),
        "attestation_type":attestation_type,
        "attestation_sequence":int(attestation_sequence),
        "attestation_status":attestation_status,
        "evidence_sha256":evidence_sha,
    }
    row=db.execute(text("""
      INSERT INTO shrimp_bilibili_reliability_attestations(
        certification_id,previous_attestation_id,attestation_type,
        attestation_sequence,attestation_status,evidence_snapshot,
        evidence_sha256,attestation_sha256,actor)
      VALUES(
        :certification_id,:previous_attestation_id,:attestation_type,
        :sequence,:status,CAST(:snapshot AS jsonb),
        :evidence_sha,:attestation_sha,:actor)
      ON CONFLICT (attestation_sha256) DO NOTHING
      RETURNING *
    """),{
        "certification_id":certification_id,
        "previous_attestation_id":previous_attestation_id,
        "attestation_type":attestation_type,
        "sequence":int(attestation_sequence),
        "status":attestation_status,
        "snapshot":canonical_json(evidence_snapshot),
        "evidence_sha":evidence_sha,
        "attestation_sha":_sha(material),
        "actor":actor[:200],
    }).mappings().one_or_none()
    if row is None:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_attestations
          WHERE attestation_sha256=:sha
        """),{"sha":_sha(material)}).mappings().one()
    return _ser(row)


def _latest_global_reliability() -> tuple[dict|None,dict|None,list[dict],list[dict]]:
    dashboard=trend_dashboard()
    return (
        dashboard.get("latest_global_scorecard"),
        dashboard.get("latest_global_burn"),
        dashboard.get("open_regressions") or [],
        dashboard.get("recurrence_clusters") or [],
    )


def _success_rate(success:int,breach:int)->float:
    total=int(success or 0)+int(breach or 0)
    if total<=0:
        return 100.0
    return round(int(success or 0)*100.0/total,2)


def _current_evidence() -> dict:
    score,burn,regressions,recurrences=_latest_global_reliability()
    with engine.connect() as db:
        unresolved_incidents=int(db.execute(text("""
          SELECT COUNT(*) FROM shrimp_bilibili_incidents
          WHERE incident_status<>'RESOLVED'
        """)).scalar_one())
        nonclosed_circuits=int(db.execute(text("""
          SELECT COUNT(*) FROM shrimp_bilibili_account_circuit_breakers
          WHERE circuit_status IN ('OPEN','RECOVERY_PENDING')
        """)).scalar_one())
    if score is None:
        return {
            "scorecard_present":False,
            "burn_present":burn is not None,
            "provider_writes":False,
        }

    metrics=score.get("metrics_payload") or {}
    ack_success=int(score.get("ack_slo_success_count") or 0)
    ack_breach=int(score.get("ack_slo_breach_count") or 0)
    recovery_success=int(score.get("recovery_slo_success_count") or 0)
    recovery_breach=int(score.get("recovery_slo_breach_count") or 0)
    return {
        "scorecard_present":True,
        "scorecard_id":score.get("id"),
        "scorecard_sha256":score.get("scorecard_sha256"),
        "generated_at":score.get("generated_at"),
        "window_start":score.get("window_start"),
        "window_end":score.get("window_end"),
        "reliability_score":float(score.get("reliability_score") or 0),
        "reliability_grade":score.get("reliability_grade"),
        "ack_success_rate":_success_rate(ack_success,ack_breach),
        "recovery_success_rate":_success_rate(recovery_success,recovery_breach),
        "ambiguity_rate_percent":float(score.get("ambiguity_rate_percent") or 0),
        "avg_ack_minutes":(
            float(score["avg_ack_minutes"])
            if score.get("avg_ack_minutes") is not None else None
        ),
        "p95_ack_minutes":(
            float(score["p95_ack_minutes"])
            if score.get("p95_ack_minutes") is not None else None
        ),
        "avg_mttr_minutes":(
            float(score["avg_mttr_minutes"])
            if score.get("avg_mttr_minutes") is not None else None
        ),
        "p95_mttr_minutes":(
            float(score["p95_mttr_minutes"])
            if score.get("p95_mttr_minutes") is not None else None
        ),
        "ack_error_budget_remaining":int(
            score.get("ack_error_budget_remaining") or 0
        ),
        "recovery_error_budget_remaining":int(
            score.get("recovery_error_budget_remaining") or 0
        ),
        "circuit_open_count":int(score.get("circuit_open_count") or 0),
        "recurring_root_cause_count":int(
            score.get("recurring_root_cause_count") or 0
        ),
        "publisher_execution_count":int(
            score.get("publisher_execution_count") or 0
        ),
        "burn_status":burn.get("burn_status") if burn else None,
        "burn_evidence_sha256":burn.get("evidence_sha256") if burn else None,
        "open_regression_count":len(regressions),
        "open_regression_ids":[x["id"] for x in regressions],
        "recurring_cluster_count":sum(
            1 for x in recurrences
            if x.get("recurrence_status")=="RECURRING"
        ),
        "unresolved_incident_count":unresolved_incidents,
        "nonclosed_circuit_count":nonclosed_circuits,
        "metrics_payload_sha256":_sha(metrics),
        "provider_writes":False,
    }


def _promoted_slo() -> dict:
    return {
        "schema_version":"shrimp-bilibili-long-term-slo-v0.1",
        "ack_success_target_percent":float(
            settings.shrimp_bilibili_ack_slo_target_percent
        ),
        "recovery_success_target_percent":float(
            settings.shrimp_bilibili_recovery_slo_target_percent
        ),
        "ambiguity_max_percent":float(
            settings.shrimp_bilibili_ambiguity_target_percent
        ),
        "reliability_score_min":85.0,
        "burn_required":"HEALTHY",
        "open_regression_max":0,
        "recurring_root_cause_max":0,
        "nonclosed_circuit_max":0,
        "unresolved_incident_max":0,
    }


def _reopen_policy(baseline:dict, promoted_slo:dict)->dict:
    baseline_score=float(baseline["reliability_score"])
    return {
        "schema_version":"shrimp-bilibili-certification-reopen-policy-v0.1",
        "reliability_score_floor":max(
            float(promoted_slo["reliability_score_min"]),
            round(baseline_score-10.0,2),
        ),
        "reliability_score_drop_points":10.0,
        "ack_success_below_percent":float(
            promoted_slo["ack_success_target_percent"]
        ),
        "recovery_success_below_percent":float(
            promoted_slo["recovery_success_target_percent"]
        ),
        "ambiguity_above_percent":float(
            promoted_slo["ambiguity_max_percent"]
        ),
        "burn_not_healthy":True,
        "open_regression_above":0,
        "recurring_root_cause_above":0,
        "nonclosed_circuit_above":0,
        "unresolved_incident_above":0,
        "automatic_policy_change":False,
        "automatic_provider_write":False,
        "human_governance_required":True,
    }


def generate_certification(
    session_id:UUID,
    *,
    actor:str,
)->dict:
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
        raise RuntimeError("Restore acceptance not found")
    if session["session_status"]!="ACCEPTED":
        raise RuntimeError("Observation session is not ACCEPTED")
    if acceptance["acceptance_status"]!="ACCEPTED":
        raise RuntimeError("Restore acceptance is not ACCEPTED")

    control=get_policy_control()
    if (
        control["automation_exposure"]!="NORMAL"
        or int(control["quota_multiplier_percent"])!=100
        or not bool(control["new_reservation_allowed"])
    ):
        raise RuntimeError("Reliability Policy Control is not fully NORMAL")

    evidence=_current_evidence()
    if not evidence.get("scorecard_present"):
        raise RuntimeError("Global Reliability Scorecard is required")
    if evidence.get("burn_status")!="HEALTHY":
        raise RuntimeError("Certification requires HEALTHY error budget burn")
    if evidence["open_regression_count"]>0:
        raise RuntimeError("Certification requires zero open regressions")
    if evidence["recurring_cluster_count"]>0:
        raise RuntimeError("Certification requires zero recurring root causes")
    if evidence["unresolved_incident_count"]>0:
        raise RuntimeError("Certification requires zero unresolved incidents")
    if evidence["nonclosed_circuit_count"]>0:
        raise RuntimeError("Certification requires all circuits CLOSED")

    slo=_promoted_slo()
    if evidence["reliability_score"]<float(slo["reliability_score_min"]):
        raise RuntimeError("Certification reliability score is below long-term minimum")
    if evidence["ack_success_rate"]<float(slo["ack_success_target_percent"]):
        raise RuntimeError("Certification ACK SLO is below promoted target")
    if evidence["recovery_success_rate"]<float(
        slo["recovery_success_target_percent"]
    ):
        raise RuntimeError("Certification recovery SLO is below promoted target")
    if evidence["ambiguity_rate_percent"]>float(slo["ambiguity_max_percent"]):
        raise RuntimeError("Certification ambiguity rate exceeds promoted target")

    baseline={
        "schema_version":"shrimp-bilibili-stability-baseline-v0.1",
        "reliability_score":evidence["reliability_score"],
        "reliability_grade":evidence["reliability_grade"],
        "ack_success_rate":evidence["ack_success_rate"],
        "recovery_success_rate":evidence["recovery_success_rate"],
        "ambiguity_rate_percent":evidence["ambiguity_rate_percent"],
        "avg_ack_minutes":evidence["avg_ack_minutes"],
        "p95_ack_minutes":evidence["p95_ack_minutes"],
        "avg_mttr_minutes":evidence["avg_mttr_minutes"],
        "p95_mttr_minutes":evidence["p95_mttr_minutes"],
        "ack_error_budget_remaining":evidence["ack_error_budget_remaining"],
        "recovery_error_budget_remaining":evidence[
            "recovery_error_budget_remaining"
        ],
        "circuit_open_count":evidence["circuit_open_count"],
        "recurring_root_cause_count":evidence[
            "recurring_root_cause_count"
        ],
        "publisher_execution_count":evidence["publisher_execution_count"],
        "scorecard_sha256":evidence["scorecard_sha256"],
        "burn_evidence_sha256":evidence["burn_evidence_sha256"],
    }
    reopen=_reopen_policy(baseline,slo)
    certification_snapshot={
        "schema_version":"shrimp-bilibili-post-restore-certification-v0.1",
        "observation_session_id":str(session_id),
        "restore_plan_id":str(session["restore_plan_id"]),
        "restore_acceptance_id":str(acceptance["id"]),
        "restore_acceptance_sha256":acceptance["acceptance_sha256"],
        "current_policy_control":{
            "automation_exposure":control["automation_exposure"],
            "quota_multiplier_percent":int(
                control["quota_multiplier_percent"]
            ),
            "new_reservation_allowed":bool(
                control["new_reservation_allowed"]
            ),
            "control_version":int(control["control_version"]),
        },
        "current_reliability_evidence":evidence,
        "stability_baseline_sha256":_sha(baseline),
        "promoted_slo":slo,
        "reopen_policy":reopen,
        "provider_writes":False,
    }
    cert_sha=_sha(certification_snapshot)
    baseline_sha=_sha(baseline)
    cert_key="cert-"+cert_sha[:20]

    with engine.begin() as db:
        existing=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_restore_certifications
          WHERE observation_session_id=:session_id
          ORDER BY certified_at DESC,id DESC
          LIMIT 1
        """),{"session_id":session_id}).mappings().one_or_none()
        if existing is not None:
            return _ser(existing)

        previous=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_restore_certifications
          WHERE certification_status IN (
            'CERTIFIED','EXPIRING','EXPIRED',
            'RECERTIFICATION_REQUIRED','REOPEN_RECOMMENDED'
          )
          ORDER BY certified_at DESC,id DESC
          LIMIT 1
        """)).mappings().one_or_none()
        previous_id=previous["id"] if previous is not None else None
        sequence=(
            int(previous["attestation_sequence"])+1
            if previous is not None else 1
        )
        previous_attestation=db.execute(text("""
          SELECT id
          FROM shrimp_bilibili_reliability_attestations
          ORDER BY created_at DESC,id DESC
          LIMIT 1
        """)).scalar_one_or_none()
        valid_from,renewal_due,expires_at=_certification_validity()

        db.execute(text("""
          UPDATE shrimp_bilibili_post_restore_certifications
          SET certification_status='SUPERSEDED',superseded_at=now()
          WHERE certification_status IN (
            'CERTIFIED','EXPIRING','EXPIRED',
            'RECERTIFICATION_REQUIRED','REOPEN_RECOMMENDED'
          )
        """))
        db.execute(text("""
          UPDATE shrimp_bilibili_certification_reopen_events
          SET event_status='SUPERSEDED',closed_at=now()
          WHERE event_status='OPEN'
        """))

        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_post_restore_certifications(
            certification_key,observation_session_id,restore_acceptance_id,
            certification_status,certification_snapshot,stability_baseline,
            promoted_slo,reopen_policy,certification_sha256,baseline_sha256,
            valid_from,renewal_due_at,expires_at,previous_certification_id,
            attestation_sequence,generated_by)
          VALUES(
            :key,:session_id,:acceptance_id,'CERTIFIED',
            CAST(:snapshot AS jsonb),CAST(:baseline AS jsonb),
            CAST(:slo AS jsonb),CAST(:reopen AS jsonb),
            :cert_sha,:baseline_sha,:valid_from,:renewal_due,:expires_at,
            :previous_id,:sequence,:actor)
          RETURNING *
        """),{
            "key":cert_key,
            "session_id":session_id,
            "acceptance_id":acceptance["id"],
            "snapshot":canonical_json(certification_snapshot),
            "baseline":canonical_json(baseline),
            "slo":canonical_json(slo),
            "reopen":canonical_json(reopen),
            "cert_sha":cert_sha,
            "baseline_sha":baseline_sha,
            "valid_from":valid_from,
            "renewal_due":renewal_due,
            "expires_at":expires_at,
            "previous_id":previous_id,
            "sequence":sequence,
            "actor":actor[:200],
        }).mappings().one()
        _append_attestation(
            db,
            certification_id=row["id"],
            previous_attestation_id=previous_attestation,
            attestation_type="INITIAL_CERTIFICATION",
            attestation_sequence=sequence,
            attestation_status="VALID",
            evidence_snapshot={
                "certification_key":row["certification_key"],
                "certification_sha256":row["certification_sha256"],
                "baseline_sha256":row["baseline_sha256"],
                "valid_from":valid_from.isoformat(),
                "renewal_due_at":renewal_due.isoformat(),
                "expires_at":expires_at.isoformat(),
                "previous_certification_id":(
                    str(previous_id) if previous_id else None
                ),
            },
            actor=actor,
        )
    return _ser(row)


def list_certifications(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_restore_certifications
          ORDER BY certified_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def get_certification(certification_id:UUID)->dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_restore_certifications
          WHERE id=:id
        """),{"id":certification_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Post-restore certification not found")
    return _ser(row)


def _trigger_codes(cert:dict,current:dict)->list[str]:
    policy=cert["reopen_policy"]
    baseline=cert["stability_baseline"]
    triggers=[]
    if not current.get("scorecard_present"):
        return ["SCORECARD_MISSING"]
    if current["reliability_score"]<float(policy["reliability_score_floor"]):
        triggers.append("RELIABILITY_SCORE_BELOW_FLOOR")
    if (
        float(baseline["reliability_score"])-current["reliability_score"]
        >=float(policy["reliability_score_drop_points"])
    ):
        triggers.append("RELIABILITY_SCORE_REGRESSION")
    if current["ack_success_rate"]<float(policy["ack_success_below_percent"]):
        triggers.append("ACK_SLO_BREACH")
    if current["recovery_success_rate"]<float(
        policy["recovery_success_below_percent"]
    ):
        triggers.append("RECOVERY_SLO_BREACH")
    if current["ambiguity_rate_percent"]>float(
        policy["ambiguity_above_percent"]
    ):
        triggers.append("AMBIGUITY_SLO_BREACH")
    if policy["burn_not_healthy"] and current.get("burn_status")!="HEALTHY":
        triggers.append("ERROR_BUDGET_BURN")
    if current["open_regression_count"]>int(policy["open_regression_above"]):
        triggers.append("OPEN_REGRESSION")
    if current["recurring_cluster_count"]>int(
        policy["recurring_root_cause_above"]
    ):
        triggers.append("ROOT_CAUSE_RECURRENCE")
    if current["nonclosed_circuit_count"]>int(
        policy["nonclosed_circuit_above"]
    ):
        triggers.append("CIRCUIT_REOPENED")
    if current["unresolved_incident_count"]>int(
        policy["unresolved_incident_above"]
    ):
        triggers.append("INCIDENT_REOPENED")
    return triggers


def evaluate_certification(*,actor:str)->dict:
    with engine.connect() as db:
        cert=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_restore_certifications
          WHERE certification_status IN (
            'CERTIFIED','EXPIRING','REOPEN_RECOMMENDED'
          )
          ORDER BY certified_at DESC
          LIMIT 1
        """)).mappings().one_or_none()
    if cert is None:
        raise RuntimeError("No active post-restore certification")

    cert=dict(cert)
    current=_current_evidence()
    triggers=_trigger_codes(cert,current)
    status="REOPEN_RECOMMENDED" if triggers else "STABLE"
    evidence_sha=_sha(current)
    material={
        "certification_id":str(cert["id"]),
        "certification_sha256":cert["certification_sha256"],
        "baseline_sha256":cert["baseline_sha256"],
        "evaluation_status":status,
        "trigger_codes":triggers,
        "current_evidence_sha256":evidence_sha,
    }
    evaluation_sha=_sha(material)

    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_certification_reopen_evaluations(
            certification_id,evaluation_status,trigger_codes,
            current_evidence,current_evidence_sha256,evaluation_sha256,
            evaluated_by)
          VALUES(
            :certification_id,:status,CAST(:triggers AS jsonb),
            CAST(:evidence AS jsonb),:evidence_sha,:evaluation_sha,:actor)
          ON CONFLICT (evaluation_sha256) DO NOTHING
          RETURNING *
        """),{
            "certification_id":cert["id"],
            "status":status,
            "triggers":canonical_json(triggers),
            "evidence":canonical_json(current),
            "evidence_sha":evidence_sha,
            "evaluation_sha":evaluation_sha,
            "actor":actor[:200],
        }).mappings().one_or_none()
        if row is None:
            row=db.execute(text("""
              SELECT * FROM shrimp_bilibili_certification_reopen_evaluations
              WHERE evaluation_sha256=:sha
            """),{"sha":evaluation_sha}).mappings().one()

        if status=="REOPEN_RECOMMENDED":
            db.execute(text("""
              UPDATE shrimp_bilibili_post_restore_certifications
              SET certification_status='REOPEN_RECOMMENDED'
              WHERE id=:id AND certification_status IN ('CERTIFIED','EXPIRING')
            """),{"id":cert["id"]})
            existing_attestation=db.execute(text("""
              SELECT id
              FROM shrimp_bilibili_reliability_attestations
              WHERE certification_id=:id
                AND attestation_type='REOPEN_RECOMMENDED'
              ORDER BY created_at DESC
              LIMIT 1
            """),{"id":cert["id"]}).scalar_one_or_none()
            if existing_attestation is None:
                previous_attestation=db.execute(text("""
                  SELECT id
                  FROM shrimp_bilibili_reliability_attestations
                  ORDER BY created_at DESC,id DESC
                  LIMIT 1
                """)).scalar_one_or_none()
                _append_attestation(
                    db,
                    certification_id=cert["id"],
                    previous_attestation_id=previous_attestation,
                    attestation_type="REOPEN_RECOMMENDED",
                    attestation_sequence=int(cert.get("attestation_sequence") or 1),
                    attestation_status="REOPENED",
                    evidence_snapshot={
                        "certification_key":cert["certification_key"],
                        "evaluation_sha256":row["evaluation_sha256"],
                        "trigger_codes":triggers,
                        "current_evidence_sha256":evidence_sha,
                    },
                    actor=actor,
                )
            event_material={
                "certification_id":str(cert["id"]),
                "evaluation_id":str(row["id"]),
                "trigger_codes":triggers,
                "evidence_sha256":evidence_sha,
            }
            db.execute(text("""
              INSERT INTO shrimp_bilibili_certification_reopen_events(
                certification_id,evaluation_id,event_status,trigger_codes,
                evidence_sha256,event_sha256,opened_by)
              VALUES(
                :certification_id,:evaluation_id,'OPEN',
                CAST(:triggers AS jsonb),:evidence_sha,:event_sha,:actor)
              ON CONFLICT (certification_id)
                WHERE event_status='OPEN'
              DO NOTHING
            """),{
                "certification_id":cert["id"],
                "evaluation_id":row["id"],
                "triggers":canonical_json(triggers),
                "evidence_sha":evidence_sha,
                "event_sha":_sha(event_material),
                "actor":actor[:200],
            })
    notification=None
    if status=="REOPEN_RECOMMENDED":
        notification=queue_notification(
            incident_id=None,
            notification_type="RELIABILITY_CERTIFICATION_REOPEN",
            severity="CRITICAL" if any(
                code in {
                    "ERROR_BUDGET_BURN",
                    "CIRCUIT_REOPENED",
                    "INCIDENT_REOPENED",
                    "ROOT_CAUSE_RECURRENCE",
                }
                for code in triggers
            ) else "WARNING",
            payload={
                "certification_id":str(cert["id"]),
                "certification_key":cert["certification_key"],
                "baseline_sha256":cert["baseline_sha256"],
                "evaluation_sha256":row["evaluation_sha256"],
                "trigger_codes":triggers,
                "action":"OPEN_GOVERNANCE_REVIEW",
                "automatic_policy_change":False,
            },
        )
    return {
        "certification_id":str(cert["id"]),
        "evaluation":_ser(row),
        "reopen_recommended":status=="REOPEN_RECOMMENDED",
        "trigger_codes":triggers,
        "notification":notification,
        "automatic_policy_change":False,
        "provider_write_count":0,
    }


def list_reopen_evaluations(*,limit:int=200)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT e.*,c.certification_key
          FROM shrimp_bilibili_certification_reopen_evaluations e
          JOIN shrimp_bilibili_post_restore_certifications c
            ON c.id=e.certification_id
          ORDER BY e.evaluated_at DESC,e.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),1000))}).mappings().all()
    return [_ser(x) for x in rows]


def list_reopen_events(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT e.*,c.certification_key
          FROM shrimp_bilibili_certification_reopen_events e
          JOIN shrimp_bilibili_post_restore_certifications c
            ON c.id=e.certification_id
          ORDER BY e.opened_at DESC,e.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def run_certification_cycle(*,actor:str)->dict:
    generated=None
    with engine.connect() as db:
        accepted=db.execute(text("""
          SELECT s.id
          FROM shrimp_bilibili_post_unfreeze_observation_sessions s
          JOIN shrimp_bilibili_restore_acceptances a ON a.session_id=s.id
          LEFT JOIN shrimp_bilibili_post_restore_certifications c
            ON c.observation_session_id=s.id
          WHERE s.session_status='ACCEPTED'
            AND a.acceptance_status='ACCEPTED'
            AND c.id IS NULL
          ORDER BY s.completed_at DESC NULLS LAST,s.created_at DESC
          LIMIT 1
        """)).scalar_one_or_none()
    if accepted is not None:
        try:
            generated=generate_certification(
                accepted,
                actor=actor+"-certification",
            )
        except RuntimeError as exc:
            generated={"status":"NOT_ELIGIBLE","reason":str(exc)}

    evaluation=None
    with engine.connect() as db:
        active=db.execute(text("""
          SELECT id FROM shrimp_bilibili_post_restore_certifications
          WHERE certification_status IN ('CERTIFIED','REOPEN_RECOMMENDED')
          ORDER BY certified_at DESC
          LIMIT 1
        """)).scalar_one_or_none()
    if active is not None:
        evaluation=evaluate_certification(actor=actor+"-reopen-evaluator")

    return {
        "generated":generated,
        "evaluation":evaluation,
        "automatic_policy_change":False,
        "provider_write_count":0,
    }


def certification_dashboard()->dict:
    certifications=list_certifications(limit=100)
    current=next((
        x for x in certifications
        if x["certification_status"] in {
            "CERTIFIED","EXPIRING","REOPEN_RECOMMENDED"
        }
    ),None)
    return {
        "current_certification":current,
        "certifications":certifications,
        "reopen_evaluations":list_reopen_evaluations(limit=200),
        "reopen_events":list_reopen_events(limit=100),
        "baseline_immutable":True,
        "long_term_slo_promoted":current is not None,
        "automatic_policy_change":False,
        "human_governance_required_for_reopen":True,
        "provider_writes":False,
        "secrets_redacted":True,
    }
