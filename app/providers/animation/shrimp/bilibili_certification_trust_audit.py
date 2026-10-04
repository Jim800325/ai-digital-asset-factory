from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_incidents import queue_notification
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    _ser,
    _sha,
)


def _attestation_expected_sha(row:dict)->tuple[str,str]:
    evidence_sha=_sha(row["evidence_snapshot"])
    material={
        "certification_id":str(row["certification_id"]),
        "previous_attestation_id":(
            str(row["previous_attestation_id"])
            if row.get("previous_attestation_id") else None
        ),
        "attestation_type":row["attestation_type"],
        "attestation_sequence":int(row["attestation_sequence"]),
        "attestation_status":row["attestation_status"],
        "evidence_sha256":evidence_sha,
    }
    return evidence_sha,_sha(material)


def _certification_issues(
    certifications:list[dict],
    attestations:list[dict],
)->tuple[list[str],dict]:
    issues=[]
    cert_checks=[]
    previous_id=None
    previous_sequence=0

    for index,cert in enumerate(certifications):
        row_issues=[]
        expected_cert_sha=_sha(cert["certification_snapshot"])
        expected_baseline_sha=_sha(cert["stability_baseline"])
        if expected_cert_sha!=cert["certification_sha256"]:
            row_issues.append("CERTIFICATION_SHA_MISMATCH")
        if expected_baseline_sha!=cert["baseline_sha256"]:
            row_issues.append("BASELINE_SHA_MISMATCH")
        if index==0:
            if cert.get("previous_certification_id") is not None:
                row_issues.append("FIRST_CERTIFICATION_HAS_PREVIOUS")
        else:
            if str(cert.get("previous_certification_id") or "")!=str(previous_id):
                row_issues.append("CERTIFICATION_CHAIN_POINTER_MISMATCH")
            if int(cert["attestation_sequence"])<=previous_sequence:
                row_issues.append("CERTIFICATION_SEQUENCE_NOT_INCREASING")
        previous_id=cert["id"]
        previous_sequence=int(cert["attestation_sequence"])
        issues.extend(row_issues)
        cert_checks.append({
            "certification_id":str(cert["id"]),
            "certification_key":cert["certification_key"],
            "certification_status":cert["certification_status"],
            "attestation_sequence":int(cert["attestation_sequence"]),
            "stored_certification_sha256":cert["certification_sha256"],
            "expected_certification_sha256":expected_cert_sha,
            "stored_baseline_sha256":cert["baseline_sha256"],
            "expected_baseline_sha256":expected_baseline_sha,
            "issues":row_issues,
        })

    att_checks=[]
    previous_attestation_id=None
    previous_attestation_created=None
    for index,att in enumerate(attestations):
        row_issues=[]
        expected_evidence_sha,expected_att_sha=_attestation_expected_sha(att)
        if expected_evidence_sha!=att["evidence_sha256"]:
            row_issues.append("ATTESTATION_EVIDENCE_SHA_MISMATCH")
        if expected_att_sha!=att["attestation_sha256"]:
            row_issues.append("ATTESTATION_SHA_MISMATCH")
        if index==0:
            if att.get("previous_attestation_id") is not None:
                row_issues.append("FIRST_ATTESTATION_HAS_PREVIOUS")
        else:
            if str(att.get("previous_attestation_id") or "")!=str(
                previous_attestation_id
            ):
                row_issues.append("ATTESTATION_CHAIN_POINTER_MISMATCH")
            if (
                previous_attestation_created is not None
                and att["created_at"]<previous_attestation_created
            ):
                row_issues.append("ATTESTATION_TIME_ORDER_INVALID")
        previous_attestation_id=att["id"]
        previous_attestation_created=att["created_at"]
        issues.extend(row_issues)
        att_checks.append({
            "attestation_id":str(att["id"]),
            "certification_id":str(att["certification_id"]),
            "attestation_type":att["attestation_type"],
            "attestation_sequence":int(att["attestation_sequence"]),
            "stored_evidence_sha256":att["evidence_sha256"],
            "expected_evidence_sha256":expected_evidence_sha,
            "stored_attestation_sha256":att["attestation_sha256"],
            "expected_attestation_sha256":expected_att_sha,
            "issues":row_issues,
        })

    current=[
        x for x in certifications
        if x["certification_status"]!="SUPERSEDED"
    ]
    if len(current)>1:
        issues.append("MULTIPLE_CURRENT_CERTIFICATIONS")

    known_cert_ids={str(x["id"]) for x in certifications}
    for att in attestations:
        if str(att["certification_id"]) not in known_cert_ids:
            issues.append("ATTESTATION_CERTIFICATION_MISSING")

    unique_issues=sorted(set(issues))
    return unique_issues,{
        "certification_checks":cert_checks,
        "attestation_checks":att_checks,
        "current_certification_ids":[str(x["id"]) for x in current],
    }


def run_integrity_audit(*,actor:str)->dict:
    with engine.connect() as db:
        certifications=[
            dict(x) for x in db.execute(text("""
              SELECT *
              FROM shrimp_bilibili_post_restore_certifications
              ORDER BY certified_at,id
            """)).mappings().all()
        ]
        attestations=[
            dict(x) for x in db.execute(text("""
              SELECT *
              FROM shrimp_bilibili_reliability_attestations
              ORDER BY created_at,id
            """)).mappings().all()
        ]

    issues,checks=_certification_issues(certifications,attestations)
    snapshot={
        "schema_version":"shrimp-bilibili-certification-integrity-audit-v0.1",
        "certification_count":len(certifications),
        "attestation_count":len(attestations),
        "current_certification_count":len(checks["current_certification_ids"]),
        "issue_codes":issues,
        "checks":checks,
        "provider_writes":False,
        "automatic_policy_change":False,
    }
    snapshot_sha=_sha(snapshot)
    material={
        "audit_status":"PASS" if not issues else "FAIL",
        "audit_snapshot_sha256":snapshot_sha,
    }
    audit_sha=_sha(material)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_certification_integrity_audits(
            audit_status,certification_count,attestation_count,
            current_certification_count,issue_codes,audit_snapshot,
            audit_snapshot_sha256,audit_sha256,evaluated_by)
          VALUES(
            :status,:cert_count,:att_count,:current_count,
            CAST(:issues AS jsonb),CAST(:snapshot AS jsonb),
            :snapshot_sha,:audit_sha,:actor)
          ON CONFLICT (audit_sha256) DO NOTHING
          RETURNING *
        """),{
            "status":"PASS" if not issues else "FAIL",
            "cert_count":len(certifications),
            "att_count":len(attestations),
            "current_count":len(checks["current_certification_ids"]),
            "issues":canonical_json(issues),
            "snapshot":canonical_json(snapshot),
            "snapshot_sha":snapshot_sha,
            "audit_sha":audit_sha,
            "actor":actor[:200],
        }).mappings().one_or_none()
        if row is None:
            row=db.execute(text("""
              SELECT *
              FROM shrimp_bilibili_certification_integrity_audits
              WHERE audit_sha256=:sha
            """),{"sha":audit_sha}).mappings().one()

    notification=None
    if issues:
        notification=queue_notification(
            incident_id=None,
            notification_type="RELIABILITY_CERTIFICATION_INTEGRITY_FAILURE",
            severity="CRITICAL",
            payload={
                "audit_id":str(row["id"]),
                "audit_sha256":row["audit_sha256"],
                "issue_codes":issues,
                "action":"OPEN_GOVERNANCE_REVIEW",
                "automatic_policy_change":False,
            },
        )
    return {
        "audit":_ser(row),
        "notification":notification,
        "automatic_policy_change":False,
        "provider_write_count":0,
    }


def list_integrity_audits(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_certification_integrity_audits
          ORDER BY evaluated_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def _close_superseded_escalations(db)->None:
    db.execute(text("""
      UPDATE shrimp_bilibili_renewal_sla_escalations e
      SET escalation_status='SUPERSEDED',closed_at=now()
      FROM shrimp_bilibili_post_restore_certifications c
      WHERE e.certification_id=c.id
        AND e.escalation_status='OPEN'
        AND c.certification_status='SUPERSEDED'
    """))


def _upsert_escalation(
    *,
    certification:dict,
    escalation_type:str,
    severity:str,
    due_at:datetime,
    now:datetime,
    actor:str,
)->dict:
    overdue=max(0,int((now-due_at).total_seconds()//60))
    evidence={
        "schema_version":"shrimp-bilibili-renewal-sla-evidence-v0.1",
        "certification_id":str(certification["id"]),
        "certification_key":certification["certification_key"],
        "certification_status":certification["certification_status"],
        "certification_sha256":certification["certification_sha256"],
        "baseline_sha256":certification["baseline_sha256"],
        "renewal_due_at":certification["renewal_due_at"].isoformat(),
        "expires_at":certification["expires_at"].isoformat(),
        "due_at":due_at.isoformat(),
        "observed_at":now.isoformat(),
        "overdue_minutes":overdue,
    }
    evidence_sha=_sha(evidence)
    material={
        "certification_id":str(certification["id"]),
        "escalation_type":escalation_type,
        "severity":severity,
        "due_at":due_at.isoformat(),
        "evidence_sha256":evidence_sha,
    }
    escalation_sha=_sha(material)
    with engine.begin() as db:
        _close_superseded_escalations(db)
        existing=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_renewal_sla_escalations
          WHERE certification_id=:certification_id
            AND escalation_type=:escalation_type
            AND escalation_status='OPEN'
          ORDER BY opened_at DESC
          LIMIT 1
        """),{
            "certification_id":certification["id"],
            "escalation_type":escalation_type,
        }).mappings().one_or_none()
        if existing is not None:
            result=_ser(existing)
            result["_created"]=False
            return result
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_renewal_sla_escalations(
            certification_id,escalation_type,severity,escalation_status,
            due_at,breached_at,overdue_minutes,evidence_snapshot,
            evidence_sha256,escalation_sha256,opened_by)
          VALUES(
            :certification_id,:type,:severity,'OPEN',
            :due_at,:breached_at,:overdue,CAST(:evidence AS jsonb),
            :evidence_sha,:escalation_sha,:actor)
          RETURNING *
        """),{
            "certification_id":certification["id"],
            "type":escalation_type,
            "severity":severity,
            "due_at":due_at,
            "breached_at":now,
            "overdue":overdue,
            "evidence":canonical_json(evidence),
            "evidence_sha":evidence_sha,
            "escalation_sha":escalation_sha,
            "actor":actor[:200],
        }).mappings().one()
    result=_ser(row)
    result["_created"]=True
    return result


def evaluate_renewal_sla(*,actor:str)->dict:
    now=datetime.now(timezone.utc)
    with engine.connect() as db:
        current=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_post_restore_certifications
          WHERE certification_status<>'SUPERSEDED'
          ORDER BY certified_at DESC,id DESC
          LIMIT 1
        """)).mappings().one_or_none()
    if current is None:
        return {
            "status":"NO_CURRENT_CERTIFICATION",
            "escalations":[],
            "provider_write_count":0,
        }

    cert=dict(current)
    escalations=[]
    sla_due=cert["renewal_due_at"]+timedelta(
        hours=max(1,int(settings.shrimp_bilibili_renewal_sla_hours))
    )
    if now>=sla_due and cert["certification_status"]!="SUPERSEDED":
        escalations.append(_upsert_escalation(
            certification=cert,
            escalation_type="RENEWAL_SLA_BREACH",
            severity="WARNING",
            due_at=sla_due,
            now=now,
            actor=actor,
        ))
    if now>=cert["expires_at"] and cert["certification_status"]!="SUPERSEDED":
        escalations.append(_upsert_escalation(
            certification=cert,
            escalation_type="MISSED_RENEWAL",
            severity="WARNING",
            due_at=cert["expires_at"],
            now=now,
            actor=actor,
        ))
    critical_due=cert["expires_at"]+timedelta(
        hours=max(1,int(settings.shrimp_bilibili_missed_renewal_critical_hours))
    )
    if now>=critical_due and cert["certification_status"]!="SUPERSEDED":
        escalations.append(_upsert_escalation(
            certification=cert,
            escalation_type="MISSED_RENEWAL_CRITICAL",
            severity="CRITICAL",
            due_at=critical_due,
            now=now,
            actor=actor,
        ))

    notifications=[]
    for item in escalations:
        if not item.get("_created"):
            continue
        notifications.append(queue_notification(
            incident_id=None,
            notification_type="RELIABILITY_CERTIFICATION_RENEWAL_SLA",
            severity=item["severity"],
            payload={
                "certification_id":item["certification_id"],
                "escalation_type":item["escalation_type"],
                "escalation_sha256":item["escalation_sha256"],
                "due_at":item["due_at"],
                "overdue_minutes":item["overdue_minutes"],
                "action":"GOVERNANCE_RECERTIFICATION_REQUIRED",
                "automatic_policy_change":False,
            },
        ))
    return {
        "status":"BREACHED" if escalations else "WITHIN_SLA",
        "certification_id":str(cert["id"]),
        "escalations":escalations,
        "notifications":notifications,
        "automatic_policy_change":False,
        "provider_write_count":0,
    }


def list_renewal_escalations(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT e.*,c.certification_key,
                 GREATEST(
                   0,
                   FLOOR(EXTRACT(EPOCH FROM (now()-e.due_at))/60)
                 )::integer AS current_overdue_minutes
          FROM shrimp_bilibili_renewal_sla_escalations e
          JOIN shrimp_bilibili_post_restore_certifications c
            ON c.id=e.certification_id
          ORDER BY e.opened_at DESC,e.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def generate_audit_proof(*,actor:str)->dict:
    audits=list_integrity_audits(limit=1)
    if not audits:
        audit_result=run_integrity_audit(actor=actor+"-integrity-audit")
        audit=audit_result["audit"]
    else:
        audit=audits[0]

    with engine.connect() as db:
        current=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_post_restore_certifications
          WHERE certification_status<>'SUPERSEDED'
          ORDER BY certified_at DESC,id DESC
          LIMIT 1
        """)).mappings().one_or_none()
        attestations=db.execute(text("""
          SELECT id,certification_id,previous_attestation_id,
                 attestation_type,attestation_sequence,attestation_status,
                 evidence_sha256,attestation_sha256,created_at
          FROM shrimp_bilibili_reliability_attestations
          ORDER BY created_at,id
        """)).mappings().all()
        escalations=db.execute(text("""
          SELECT id,certification_id,escalation_type,severity,
                 escalation_status,evidence_sha256,escalation_sha256,
                 due_at,opened_at,closed_at
          FROM shrimp_bilibili_renewal_sla_escalations
          ORDER BY opened_at,id
        """)).mappings().all()

    proof={
        "schema_version":"shrimp-bilibili-certification-audit-proof-v0.1",
        "integrity_audit":{
            "id":audit["id"],
            "audit_status":audit["audit_status"],
            "audit_sha256":audit["audit_sha256"],
            "audit_snapshot_sha256":audit["audit_snapshot_sha256"],
            "evaluated_at":audit["evaluated_at"],
        },
        "current_certification":(
            {
                "id":str(current["id"]),
                "certification_key":current["certification_key"],
                "certification_status":current["certification_status"],
                "certification_sha256":current["certification_sha256"],
                "baseline_sha256":current["baseline_sha256"],
                "attestation_sequence":int(current["attestation_sequence"]),
                "valid_from":current["valid_from"].isoformat(),
                "renewal_due_at":current["renewal_due_at"].isoformat(),
                "expires_at":current["expires_at"].isoformat(),
            }
            if current is not None else None
        ),
        "attestation_chain":[
            {
                "id":str(x["id"]),
                "certification_id":str(x["certification_id"]),
                "previous_attestation_id":(
                    str(x["previous_attestation_id"])
                    if x["previous_attestation_id"] else None
                ),
                "attestation_type":x["attestation_type"],
                "attestation_sequence":int(x["attestation_sequence"]),
                "attestation_status":x["attestation_status"],
                "evidence_sha256":x["evidence_sha256"],
                "attestation_sha256":x["attestation_sha256"],
                "created_at":x["created_at"].isoformat(),
            }
            for x in attestations
        ],
        "renewal_escalations":[
            {
                "id":str(x["id"]),
                "certification_id":str(x["certification_id"]),
                "escalation_type":x["escalation_type"],
                "severity":x["severity"],
                "escalation_status":x["escalation_status"],
                "evidence_sha256":x["evidence_sha256"],
                "escalation_sha256":x["escalation_sha256"],
                "due_at":x["due_at"].isoformat(),
                "opened_at":x["opened_at"].isoformat(),
                "closed_at":x["closed_at"].isoformat() if x["closed_at"] else None,
            }
            for x in escalations
        ],
        "automatic_policy_change":False,
        "provider_writes":False,
    }
    proof_snapshot_sha=_sha(proof)
    material={
        "integrity_audit_id":audit["id"],
        "current_certification_id":(
            str(current["id"]) if current is not None else None
        ),
        "proof_snapshot_sha256":proof_snapshot_sha,
    }
    proof_sha=_sha(material)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_certification_audit_proofs(
            integrity_audit_id,current_certification_id,
            proof_snapshot,proof_snapshot_sha256,proof_sha256,generated_by)
          VALUES(
            :audit_id,:certification_id,CAST(:snapshot AS jsonb),
            :snapshot_sha,:proof_sha,:actor)
          ON CONFLICT (proof_sha256) DO NOTHING
          RETURNING *
        """),{
            "audit_id":audit["id"],
            "certification_id":current["id"] if current is not None else None,
            "snapshot":canonical_json(proof),
            "snapshot_sha":proof_snapshot_sha,
            "proof_sha":proof_sha,
            "actor":actor[:200],
        }).mappings().one_or_none()
        if row is None:
            row=db.execute(text("""
              SELECT *
              FROM shrimp_bilibili_certification_audit_proofs
              WHERE proof_sha256=:sha
            """),{"sha":proof_sha}).mappings().one()
    return _ser(row)


def list_audit_proofs(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_certification_audit_proofs
          ORDER BY generated_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def run_trust_audit_cycle(*,actor:str)->dict:
    integrity=run_integrity_audit(actor=actor+"-integrity")
    sla=evaluate_renewal_sla(actor=actor+"-renewal-sla")
    proof=generate_audit_proof(actor=actor+"-proof")
    return {
        "integrity":integrity,
        "renewal_sla":sla,
        "audit_proof":proof,
        "automatic_policy_change":False,
        "provider_write_count":0,
    }


def trust_audit_dashboard()->dict:
    audits=list_integrity_audits(limit=100)
    escalations=list_renewal_escalations(limit=100)
    proofs=list_audit_proofs(limit=100)
    latest=audits[0] if audits else None
    return {
        "latest_integrity_audit":latest,
        "integrity_audits":audits,
        "renewal_escalations":escalations,
        "audit_proofs":proofs,
        "trust_chain_valid":bool(latest and latest["audit_status"]=="PASS"),
        "renewal_sla_hours":max(
            1,int(settings.shrimp_bilibili_renewal_sla_hours)
        ),
        "missed_renewal_critical_hours":max(
            1,int(settings.shrimp_bilibili_missed_renewal_critical_hours)
        ),
        "automatic_policy_change":False,
        "provider_writes":False,
        "secrets_redacted":True,
    }
