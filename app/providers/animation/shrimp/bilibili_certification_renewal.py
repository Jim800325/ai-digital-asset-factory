from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_incidents import queue_notification
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    _append_attestation,
    _certification_validity,
    _current_evidence,
    _promoted_slo,
    _reopen_policy,
    _ser,
    _sha,
)
from app.providers.animation.shrimp.bilibili_reliability_policy_change import (
    get_policy_control,
)


def _certification_eligible(evidence: dict, slo: dict) -> tuple[bool,list[str]]:
    blockers=[]
    if not evidence.get("scorecard_present"):
        blockers.append("SCORECARD_MISSING")
        return False,blockers
    if evidence.get("burn_status")!="HEALTHY":
        blockers.append("BURN_NOT_HEALTHY")
    if int(evidence.get("open_regression_count") or 0)>0:
        blockers.append("OPEN_REGRESSION")
    if int(evidence.get("recurring_cluster_count") or 0)>0:
        blockers.append("ROOT_CAUSE_RECURRENCE")
    if int(evidence.get("unresolved_incident_count") or 0)>0:
        blockers.append("UNRESOLVED_INCIDENT")
    if int(evidence.get("nonclosed_circuit_count") or 0)>0:
        blockers.append("NONCLOSED_CIRCUIT")
    if float(evidence.get("reliability_score") or 0)<float(
        slo["reliability_score_min"]
    ):
        blockers.append("RELIABILITY_SCORE_BELOW_SLO")
    if float(evidence.get("ack_success_rate") or 0)<float(
        slo["ack_success_target_percent"]
    ):
        blockers.append("ACK_SLO_BREACH")
    if float(evidence.get("recovery_success_rate") or 0)<float(
        slo["recovery_success_target_percent"]
    ):
        blockers.append("RECOVERY_SLO_BREACH")
    if float(evidence.get("ambiguity_rate_percent") or 0)>float(
        slo["ambiguity_max_percent"]
    ):
        blockers.append("AMBIGUITY_SLO_BREACH")
    return not blockers,blockers


def _baseline_from_evidence(evidence:dict)->dict:
    return {
        "schema_version":"shrimp-bilibili-stability-baseline-v0.2",
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
        "recurring_root_cause_count":evidence["recurring_root_cause_count"],
        "publisher_execution_count":evidence["publisher_execution_count"],
        "scorecard_sha256":evidence["scorecard_sha256"],
        "burn_evidence_sha256":evidence["burn_evidence_sha256"],
    }


def _latest_chain_attestation(db):
    return db.execute(text("""
      SELECT id FROM shrimp_bilibili_reliability_attestations
      ORDER BY created_at DESC,id DESC
      LIMIT 1
    """)).scalar_one_or_none()


def list_attestations(*,limit:int=200)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT a.*,c.certification_key
          FROM shrimp_bilibili_reliability_attestations a
          JOIN shrimp_bilibili_post_restore_certifications c
            ON c.id=a.certification_id
          ORDER BY a.created_at DESC,a.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),1000))}).mappings().all()
    return [_ser(x) for x in rows]


def list_recertification_candidates(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT r.*,c.certification_key,c.certification_status,
                 c.expires_at,c.renewal_due_at
          FROM shrimp_bilibili_recertification_candidates r
          JOIN shrimp_bilibili_post_restore_certifications c
            ON c.id=r.source_certification_id
          ORDER BY r.generated_at DESC,r.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def list_recertification_decisions(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT d.*,r.source_certification_id,c.certification_key
          FROM shrimp_bilibili_recertification_decisions d
          JOIN shrimp_bilibili_recertification_candidates r ON r.id=d.candidate_id
          JOIN shrimp_bilibili_post_restore_certifications c
            ON c.id=r.source_certification_id
          ORDER BY d.decided_at DESC,d.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def generate_recertification_candidate(
    certification_id:UUID,
    *,
    actor:str,
)->dict:
    with engine.connect() as db:
        source=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_restore_certifications
          WHERE id=:id
        """),{"id":certification_id}).mappings().one_or_none()
    if source is None:
        raise LookupError("Certification not found")
    source=dict(source)
    if source["certification_status"] not in {
        "CERTIFIED","EXPIRING","EXPIRED","RECERTIFICATION_REQUIRED"
    }:
        raise RuntimeError("Certification is not eligible for renewal")

    control=get_policy_control()
    if (
        control["automation_exposure"]!="NORMAL"
        or int(control["quota_multiplier_percent"])!=100
        or not bool(control["new_reservation_allowed"])
    ):
        raise RuntimeError("Re-certification requires fully NORMAL policy control")

    evidence=_current_evidence()
    slo=_promoted_slo()
    eligible,blockers=_certification_eligible(evidence,slo)
    if not eligible:
        raise RuntimeError(
            "Re-certification evidence is not eligible: "+",".join(blockers)
        )
    if evidence["scorecard_sha256"]==source["stability_baseline"].get(
        "scorecard_sha256"
    ):
        raise RuntimeError("Re-certification requires a newer reliability scorecard")

    baseline=_baseline_from_evidence(evidence)
    reopen=_reopen_policy(baseline,slo)
    valid_from,renewal_due,expires_at=_certification_validity()
    candidate_snapshot={
        "schema_version":"shrimp-bilibili-recertification-candidate-v0.1",
        "source_certification_id":str(source["id"]),
        "source_certification_key":source["certification_key"],
        "source_certification_sha256":source["certification_sha256"],
        "source_baseline_sha256":source["baseline_sha256"],
        "source_expires_at":source["expires_at"].isoformat(),
        "new_reliability_evidence":evidence,
        "proposed_stability_baseline_sha256":_sha(baseline),
        "proposed_valid_from":valid_from.isoformat(),
        "proposed_renewal_due_at":renewal_due.isoformat(),
        "proposed_expires_at":expires_at.isoformat(),
        "automatic_policy_change":False,
        "provider_writes":False,
    }
    new_evidence_sha=_sha(evidence)
    candidate_material={
        "source_certification_sha256":source["certification_sha256"],
        "source_baseline_sha256":source["baseline_sha256"],
        "new_evidence_sha256":new_evidence_sha,
        "baseline_sha256":_sha(baseline),
        "promoted_slo":slo,
        "reopen_policy":reopen,
        "valid_from":valid_from.isoformat(),
        "renewal_due_at":renewal_due.isoformat(),
        "expires_at":expires_at.isoformat(),
    }
    candidate_sha=_sha(candidate_material)

    with engine.begin() as db:
        existing=db.execute(text("""
          SELECT * FROM shrimp_bilibili_recertification_candidates
          WHERE source_certification_id=:source_id
            AND candidate_status='PENDING_APPROVAL'
          ORDER BY generated_at DESC
          LIMIT 1
        """),{"source_id":source["id"]}).mappings().one_or_none()
        if existing is not None:
            if existing["new_evidence_sha256"]==new_evidence_sha:
                return _ser(existing)
            db.execute(text("""
              UPDATE shrimp_bilibili_recertification_candidates
              SET candidate_status='STALE',decided_at=now()
              WHERE id=:id
            """),{"id":existing["id"]})

        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_recertification_candidates(
            source_certification_id,candidate_status,candidate_snapshot,
            proposed_stability_baseline,promoted_slo,reopen_policy,
            source_certification_sha256,source_baseline_sha256,
            new_evidence_sha256,candidate_sha256,generated_by)
          VALUES(
            :source_id,'PENDING_APPROVAL',CAST(:snapshot AS jsonb),
            CAST(:baseline AS jsonb),CAST(:slo AS jsonb),CAST(:reopen AS jsonb),
            :source_cert_sha,:source_baseline_sha,:evidence_sha,:candidate_sha,
            :actor)
          RETURNING *
        """),{
            "source_id":source["id"],
            "snapshot":canonical_json(candidate_snapshot),
            "baseline":canonical_json(baseline),
            "slo":canonical_json(slo),
            "reopen":canonical_json(reopen),
            "source_cert_sha":source["certification_sha256"],
            "source_baseline_sha":source["baseline_sha256"],
            "evidence_sha":new_evidence_sha,
            "candidate_sha":candidate_sha,
            "actor":actor[:200],
        }).mappings().one()
        if source["certification_status"]=="EXPIRED":
            db.execute(text("""
              UPDATE shrimp_bilibili_post_restore_certifications
              SET certification_status='RECERTIFICATION_REQUIRED'
              WHERE id=:id AND certification_status='EXPIRED'
            """),{"id":source["id"]})
    return _ser(row)


def _mark_attestation_once(
    *,
    certification:dict,
    attestation_type:str,
    status:str,
    actor:str,
    evidence:dict,
)->dict|None:
    with engine.begin() as db:
        existing=db.execute(text("""
          SELECT * FROM shrimp_bilibili_reliability_attestations
          WHERE certification_id=:certification_id
            AND attestation_type=:attestation_type
          ORDER BY created_at DESC
          LIMIT 1
        """),{
            "certification_id":certification["id"],
            "attestation_type":attestation_type,
        }).mappings().one_or_none()
        if existing is not None:
            return _ser(existing)
        previous=_latest_chain_attestation(db)
        return _append_attestation(
            db,
            certification_id=certification["id"],
            previous_attestation_id=previous,
            attestation_type=attestation_type,
            attestation_sequence=int(certification["attestation_sequence"]),
            attestation_status=status,
            evidence_snapshot=evidence,
            actor=actor,
        )


def evaluate_certification_expiry(*,actor:str)->dict:
    now=datetime.now(timezone.utc)
    with engine.connect() as db:
        source=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_restore_certifications
          WHERE certification_status IN (
            'CERTIFIED','EXPIRING','EXPIRED','RECERTIFICATION_REQUIRED'
          )
          ORDER BY certified_at DESC,id DESC
          LIMIT 1
        """)).mappings().one_or_none()
    if source is None:
        return {
            "status":"NO_ACTIVE_CERTIFICATION",
            "candidate":None,
            "provider_write_count":0,
        }
    cert=dict(source)
    action="VALID"
    notification=None

    if now>=cert["expires_at"] and cert["certification_status"] in {
        "CERTIFIED","EXPIRING"
    }:
        with engine.begin() as db:
            db.execute(text("""
              UPDATE shrimp_bilibili_post_restore_certifications
              SET certification_status='EXPIRED'
              WHERE id=:id
                AND certification_status IN ('CERTIFIED','EXPIRING')
            """),{"id":cert["id"]})
        cert["certification_status"]="EXPIRED"
        action="EXPIRED"
        expiry_evidence={
            "certification_key":cert["certification_key"],
            "certification_sha256":cert["certification_sha256"],
            "baseline_sha256":cert["baseline_sha256"],
            "expired_at":cert["expires_at"].isoformat(),
            "observed_at":now.isoformat(),
            "requires_recertification":True,
        }
        _mark_attestation_once(
            certification=cert,
            attestation_type="CERTIFICATION_EXPIRED",
            status="EXPIRED",
            actor=actor,
            evidence=expiry_evidence,
        )
        notification=queue_notification(
            incident_id=None,
            notification_type="RELIABILITY_CERTIFICATION_EXPIRED",
            severity="WARNING",
            payload={
                **expiry_evidence,
                "action":"GOVERNANCE_RECERTIFICATION_REQUIRED",
            },
        )
    elif now>=cert["renewal_due_at"] and cert["certification_status"]=="CERTIFIED":
        with engine.begin() as db:
            db.execute(text("""
              UPDATE shrimp_bilibili_post_restore_certifications
              SET certification_status='EXPIRING'
              WHERE id=:id AND certification_status='CERTIFIED'
            """),{"id":cert["id"]})
        cert["certification_status"]="EXPIRING"
        action="EXPIRING"
        warning_evidence={
            "certification_key":cert["certification_key"],
            "certification_sha256":cert["certification_sha256"],
            "baseline_sha256":cert["baseline_sha256"],
            "renewal_due_at":cert["renewal_due_at"].isoformat(),
            "expires_at":cert["expires_at"].isoformat(),
            "observed_at":now.isoformat(),
        }
        _mark_attestation_once(
            certification=cert,
            attestation_type="EXPIRY_WARNING",
            status="WARNING",
            actor=actor,
            evidence=warning_evidence,
        )
        notification=queue_notification(
            incident_id=None,
            notification_type="RELIABILITY_CERTIFICATION_EXPIRING",
            severity="WARNING",
            payload={
                **warning_evidence,
                "action":"PREPARE_GOVERNANCE_RECERTIFICATION",
            },
        )

    candidate=None
    if cert["certification_status"] in {
        "EXPIRING","EXPIRED","RECERTIFICATION_REQUIRED"
    }:
        try:
            candidate=generate_recertification_candidate(
                cert["id"],
                actor=actor+"-candidate",
            )
        except RuntimeError as exc:
            candidate={"status":"NOT_ELIGIBLE","reason":str(exc)}

    return {
        "status":action,
        "certification":_ser(cert),
        "candidate":candidate,
        "notification":notification,
        "provider_write_count":0,
        "automatic_policy_change":False,
    }


def decide_recertification(
    candidate_id:UUID,
    *,
    decision:str,
    reason:str,
    actor:str,
    candidate_sha256:str,
)->dict:
    clean_reason=reason.strip()
    if len(clean_reason)<3:
        raise ValueError("Re-certification decision reason is required")
    if decision not in {"APPROVE","REJECT"}:
        raise ValueError("decision must be APPROVE or REJECT")

    with engine.connect() as db:
        candidate=db.execute(text("""
          SELECT r.*,c.observation_session_id,c.restore_acceptance_id,
                 c.certification_key,c.certification_status,
                 c.attestation_sequence,c.expires_at
          FROM shrimp_bilibili_recertification_candidates r
          JOIN shrimp_bilibili_post_restore_certifications c
            ON c.id=r.source_certification_id
          WHERE r.id=:id
        """),{"id":candidate_id}).mappings().one_or_none()
    if candidate is None:
        raise LookupError("Re-certification candidate not found")
    candidate=dict(candidate)
    if candidate["candidate_status"]!="PENDING_APPROVAL":
        raise RuntimeError("Re-certification candidate is not pending")
    if candidate_sha256!=candidate["candidate_sha256"]:
        raise RuntimeError("Re-certification candidate hash mismatch")

    current=_current_evidence()
    if _sha(current)!=candidate["new_evidence_sha256"]:
        with engine.begin() as db:
            db.execute(text("""
              UPDATE shrimp_bilibili_recertification_candidates
              SET candidate_status='STALE',decided_at=now()
              WHERE id=:id AND candidate_status='PENDING_APPROVAL'
            """),{"id":candidate_id})
        raise RuntimeError("Re-certification evidence drifted; generate a new candidate")

    control=get_policy_control()
    if (
        control["automation_exposure"]!="NORMAL"
        or int(control["quota_multiplier_percent"])!=100
        or not bool(control["new_reservation_allowed"])
    ):
        raise RuntimeError("Re-certification requires fully NORMAL policy control")

    decision_material={
        "candidate_id":str(candidate_id),
        "candidate_sha256":candidate_sha256,
        "decision":decision,
        "reason":clean_reason,
        "actor":actor.strip(),
    }

    with engine.begin() as db:
        locked=db.execute(text("""
          SELECT * FROM shrimp_bilibili_recertification_candidates
          WHERE id=:id FOR UPDATE
        """),{"id":candidate_id}).mappings().one()
        if locked["candidate_status"]!="PENDING_APPROVAL":
            raise RuntimeError("Re-certification candidate changed during decision")
        decision_row=db.execute(text("""
          INSERT INTO shrimp_bilibili_recertification_decisions(
            candidate_id,decision,reason,actor,candidate_sha256,decision_sha256)
          VALUES(
            :candidate_id,:decision,:reason,:actor,:candidate_sha,:decision_sha)
          RETURNING *
        """),{
            "candidate_id":candidate_id,
            "decision":decision,
            "reason":clean_reason,
            "actor":actor.strip()[:200],
            "candidate_sha":candidate_sha256,
            "decision_sha":_sha(decision_material),
        }).mappings().one()

        source=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_restore_certifications
          WHERE id=:id FOR UPDATE
        """),{"id":locked["source_certification_id"]}).mappings().one()
        previous_attestation=_latest_chain_attestation(db)

        if decision=="REJECT":
            db.execute(text("""
              UPDATE shrimp_bilibili_recertification_candidates
              SET candidate_status='REJECTED',decided_at=now()
              WHERE id=:id
            """),{"id":candidate_id})
            _append_attestation(
                db,
                certification_id=source["id"],
                previous_attestation_id=previous_attestation,
                attestation_type="RECERTIFICATION_REJECTED",
                attestation_sequence=int(source["attestation_sequence"]),
                attestation_status="REJECTED",
                evidence_snapshot={
                    "candidate_id":str(candidate_id),
                    "candidate_sha256":candidate_sha256,
                    "reason":clean_reason,
                    "actor":actor.strip(),
                },
                actor=actor,
            )
            return {
                "candidate_id":str(candidate_id),
                "decision":_ser(decision_row),
                "candidate_status":"REJECTED",
                "certification_created":False,
                "provider_write_count":0,
            }

        valid_from,renewal_due,expires_at=_certification_validity()
        baseline=dict(locked["proposed_stability_baseline"])
        slo=dict(locked["promoted_slo"])
        reopen=dict(locked["reopen_policy"])
        sequence=int(source["attestation_sequence"])+1
        snapshot={
            "schema_version":"shrimp-bilibili-recertification-v0.1",
            "previous_certification_id":str(source["id"]),
            "previous_certification_key":source["certification_key"],
            "recertification_candidate_id":str(candidate_id),
            "recertification_candidate_sha256":candidate_sha256,
            "current_reliability_evidence":current,
            "stability_baseline_sha256":_sha(baseline),
            "promoted_slo":slo,
            "reopen_policy":reopen,
            "valid_from":valid_from.isoformat(),
            "renewal_due_at":renewal_due.isoformat(),
            "expires_at":expires_at.isoformat(),
            "provider_writes":False,
        }
        cert_sha=_sha(snapshot)
        cert_key="cert-"+cert_sha[:20]
        new_cert=db.execute(text("""
          INSERT INTO shrimp_bilibili_post_restore_certifications(
            certification_key,observation_session_id,restore_acceptance_id,
            certification_status,certification_snapshot,stability_baseline,
            promoted_slo,reopen_policy,certification_sha256,baseline_sha256,
            valid_from,renewal_due_at,expires_at,previous_certification_id,
            attestation_sequence,generated_by)
          VALUES(
            :key,:session_id,:acceptance_id,'CERTIFIED',
            CAST(:snapshot AS jsonb),CAST(:baseline AS jsonb),
            CAST(:slo AS jsonb),CAST(:reopen AS jsonb),:cert_sha,:baseline_sha,
            :valid_from,:renewal_due,:expires_at,:previous_id,:sequence,:actor)
          RETURNING *
        """),{
            "key":cert_key,
            "session_id":source["observation_session_id"],
            "acceptance_id":source["restore_acceptance_id"],
            "snapshot":canonical_json(snapshot),
            "baseline":canonical_json(baseline),
            "slo":canonical_json(slo),
            "reopen":canonical_json(reopen),
            "cert_sha":cert_sha,
            "baseline_sha":_sha(baseline),
            "valid_from":valid_from,
            "renewal_due":renewal_due,
            "expires_at":expires_at,
            "previous_id":source["id"],
            "sequence":sequence,
            "actor":actor.strip()[:200],
        }).mappings().one()

        db.execute(text("""
          UPDATE shrimp_bilibili_post_restore_certifications
          SET certification_status='SUPERSEDED',superseded_at=now()
          WHERE id=:id
        """),{"id":source["id"]})
        db.execute(text("""
          UPDATE shrimp_bilibili_certification_reopen_events
          SET event_status='SUPERSEDED',closed_at=now()
          WHERE certification_id=:id AND event_status='OPEN'
        """),{"id":source["id"]})
        db.execute(text("""
          UPDATE shrimp_bilibili_recertification_candidates
          SET candidate_status='APPROVED',decided_at=now()
          WHERE id=:id
        """),{"id":candidate_id})

        source_att=_append_attestation(
            db,
            certification_id=source["id"],
            previous_attestation_id=previous_attestation,
            attestation_type="SUPERSEDED",
            attestation_sequence=int(source["attestation_sequence"]),
            attestation_status="SUPERSEDED",
            evidence_snapshot={
                "source_certification_id":str(source["id"]),
                "new_certification_id":str(new_cert["id"]),
                "candidate_id":str(candidate_id),
            },
            actor=actor,
        )
        new_att=_append_attestation(
            db,
            certification_id=new_cert["id"],
            previous_attestation_id=source_att["id"],
            attestation_type="RECERTIFICATION_APPROVED",
            attestation_sequence=sequence,
            attestation_status="APPROVED",
            evidence_snapshot={
                "certification_key":new_cert["certification_key"],
                "certification_sha256":new_cert["certification_sha256"],
                "baseline_sha256":new_cert["baseline_sha256"],
                "previous_certification_id":str(source["id"]),
                "candidate_id":str(candidate_id),
                "valid_from":valid_from.isoformat(),
                "renewal_due_at":renewal_due.isoformat(),
                "expires_at":expires_at.isoformat(),
            },
            actor=actor,
        )

    return {
        "candidate_id":str(candidate_id),
        "decision":_ser(decision_row),
        "candidate_status":"APPROVED",
        "certification":_ser(new_cert),
        "attestation":new_att,
        "certification_created":True,
        "provider_write_count":0,
        "automatic_policy_change":False,
    }


def renewal_dashboard()->dict:
    now=datetime.now(timezone.utc)
    with engine.connect() as db:
        current=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_restore_certifications
          WHERE certification_status<>'SUPERSEDED'
          ORDER BY certified_at DESC,id DESC
          LIMIT 1
        """)).mappings().one_or_none()
    current_ser=_ser(current) if current is not None else None
    if current is not None:
        expires_in_days=round(
            (current["expires_at"]-now).total_seconds()/86400,2
        )
    else:
        expires_in_days=None
    return {
        "current_certification":current_ser,
        "expires_in_days":expires_in_days,
        "certification_current":bool(
            current is not None
            and current["certification_status"] in {"CERTIFIED","EXPIRING"}
            and now<current["expires_at"]
        ),
        "recertification_required":bool(
            current is not None
            and (
                current["certification_status"] in {
                    "EXPIRED","RECERTIFICATION_REQUIRED"
                }
                or now>=current["expires_at"]
            )
        ),
        "candidates":list_recertification_candidates(limit=100),
        "decisions":list_recertification_decisions(limit=100),
        "attestations":list_attestations(limit=300),
        "automatic_recertification":False,
        "human_recertification_gate_required":True,
        "provider_writes":False,
        "secrets_redacted":True,
    }
