from __future__ import annotations

import hashlib

import httpx
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_recovery_policy import (
    evaluate_account_circuit,
)

def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def _serialize(row: Any) -> dict:
    result=dict(row)
    for key in (
        "id","account_id","execution_claim_id","escalation_id",
        "circuit_breaker_id","incident_id",
    ):
        if result.get(key) is not None:
            result[key]=str(result[key])
    for key in ("opened_at","resolved_at","created_at","requested_at","decided_at"):
        if result.get(key) is not None:
            result[key]=result[key].isoformat()
    return result

def _timeline(
    db,
    *,
    incident_id,
    event_type: str,
    source_type: str,
    source_id: str | None,
    payload: dict,
    actor: str,
)->dict:
    material={
        "incident_id":str(incident_id),
        "event_type":event_type,
        "source_type":source_type,
        "source_id":source_id,
        "payload":payload,
    }
    sha=_sha256(material)
    row=db.execute(text("""
      INSERT INTO shrimp_bilibili_incident_timeline(
        incident_id,event_type,source_type,source_id,
        event_payload,event_sha256,actor)
      VALUES(
        :incident_id,:event_type,:source_type,:source_id,
        CAST(:payload AS jsonb),:sha,:actor)
      ON CONFLICT (event_sha256) DO NOTHING
      RETURNING *
    """),{
      "incident_id":incident_id,
      "event_type":event_type,
      "source_type":source_type,
      "source_id":source_id,
      "payload":canonical_json(payload),
      "sha":sha,
      "actor":actor[:200],
    }).mappings().one_or_none()
    if row is None:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_incident_timeline
          WHERE event_sha256=:sha
        """),{"sha":sha}).mappings().one()
    return _serialize(row)

def sync_critical_incidents(*,actor: str)->list[dict]:
    with engine.begin() as db:
        rows=db.execute(text("""
          SELECT e.*,a.account_key,c.id AS circuit_id,c.circuit_status
          FROM shrimp_bilibili_claim_escalations e
          JOIN shrimp_bilibili_accounts a ON a.id=e.account_id
          LEFT JOIN shrimp_bilibili_account_circuit_breakers c
            ON c.account_id=e.account_id
          WHERE e.escalation_status='OPEN'
            AND e.escalation_level='CRITICAL'
          ORDER BY e.opened_at
        """)).mappings().all()
        result=[]
        for esc in rows:
            existing=db.execute(text("""
              SELECT * FROM shrimp_bilibili_incidents
              WHERE account_id=:account_id
                AND incident_status IN ('OPEN','RECOVERY_REVIEW')
              FOR UPDATE
            """),{"account_id":esc["account_id"]}).mappings().one_or_none()
            evidence={
                "account_key":esc["account_key"],
                "account_id":str(esc["account_id"]),
                "escalation_id":str(esc["id"]),
                "execution_claim_id":str(esc["execution_claim_id"]),
                "execution_id":str(esc["execution_id"]),
                "escalation_level":esc["escalation_level"],
                "claim_age_minutes":esc["claim_age_minutes"],
                "ambiguity_count":esc["ambiguity_count"],
                "execution_status":esc["detected_execution_status"],
                "circuit_status":esc["circuit_status"],
            }
            if existing is None:
                incident_key="BILI-"+str(esc["id"])[:8].upper()
                row=db.execute(text("""
                  INSERT INTO shrimp_bilibili_incidents(
                    incident_key,account_id,execution_claim_id,escalation_id,
                    circuit_breaker_id,severity,incident_status,title,summary,
                    evidence_snapshot,evidence_sha256,opened_by)
                  VALUES(
                    :incident_key,:account_id,:claim_id,:escalation_id,
                    :circuit_id,'CRITICAL','OPEN',:title,:summary,
                    CAST(:evidence AS jsonb),:evidence_sha,:actor)
                  RETURNING *
                """),{
                  "incident_key":incident_key,
                  "account_id":esc["account_id"],
                  "claim_id":esc["execution_claim_id"],
                  "escalation_id":esc["id"],
                  "circuit_id":esc["circuit_id"],
                  "title":"Critical Bilibili publisher incident",
                  "summary":"Critical Claim escalation requires human recovery review.",
                  "evidence":canonical_json(evidence),
                  "evidence_sha":_sha256(evidence),
                  "actor":actor[:200],
                }).mappings().one()
                _timeline(
                    db,
                    incident_id=row["id"],
                    event_type="INCIDENT_OPENED",
                    source_type="CLAIM_ESCALATION",
                    source_id=str(esc["id"]),
                    payload=evidence,
                    actor=actor,
                )
                result.append(_serialize(row))
            else:
                _timeline(
                    db,
                    incident_id=existing["id"],
                    event_type="ESCALATION_LINKED",
                    source_type="CLAIM_ESCALATION",
                    source_id=str(esc["id"]),
                    payload=evidence,
                    actor=actor,
                )
                result.append(_serialize(existing))
    return result

def list_incidents(*,status:str|None=None,limit:int=100)->list[dict]:
    params={"limit":max(1,min(int(limit),500))}
    sql="""
      SELECT i.*,a.account_key,a.display_name
      FROM shrimp_bilibili_incidents i
      JOIN shrimp_bilibili_accounts a ON a.id=i.account_id
    """
    if status:
        sql+=" WHERE i.incident_status=:status"
        params["status"]=status.upper()
    sql+=" ORDER BY i.opened_at DESC,i.id DESC LIMIT :limit"
    with engine.connect() as db:
        rows=db.execute(text(sql),params).mappings().all()
    return [_serialize(x) for x in rows]

def incident_timeline(incident_id)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_incident_timeline
          WHERE incident_id=CAST(:incident_id AS uuid)
          ORDER BY created_at,id
        """),{"incident_id":incident_id}).mappings().all()
    return [_serialize(x) for x in rows]


def _recovery_snapshot(db, incident:dict)->dict:
    circuit=db.execute(text("""
      SELECT * FROM shrimp_bilibili_account_circuit_breakers
      WHERE account_id=:account_id
    """),{"account_id":incident["account_id"]}).mappings().one_or_none()
    slot=db.execute(text("""
      SELECT slot_key,slot_status,health_status,degradation_status,
             mid_status,publish_permission_status,health_evidence_sha256,
             last_checked_at
      FROM shrimp_bilibili_credential_slots
      WHERE account_id=:account_id
      LIMIT 1
    """),{"account_id":incident["account_id"]}).mappings().one_or_none()
    ambiguous=int(db.execute(text("""
      SELECT COUNT(*)
      FROM shrimp_bilibili_execution_claims c
      JOIN shrimp_animation_publish_executions e ON e.id=c.execution_id
      WHERE c.account_id=:account_id
        AND c.claim_status='CLAIMED'
        AND e.execution_status IN (
          'UPLOADING','UPLOAD_UNKNOWN','PUBLISHING','PUBLISH_UNKNOWN'
        )
    """),{"account_id":incident["account_id"]}).scalar_one())
    return {
        "incident_id":str(incident["id"]),
        "account_id":str(incident["account_id"]),
        "incident_evidence_sha256":incident["evidence_sha256"],
        "circuit":{
            "id":str(circuit["id"]) if circuit else None,
            "status":circuit["circuit_status"] if circuit else "CLOSED",
            "state_version":int(circuit["state_version"]) if circuit else 0,
            "last_evidence_sha256":(
                circuit["last_evidence_sha256"] if circuit else None
            ),
        },
        "slot":{
            "slot_key":slot["slot_key"] if slot else None,
            "slot_status":slot["slot_status"] if slot else None,
            "health_status":slot["health_status"] if slot else None,
            "degradation_status":slot["degradation_status"] if slot else None,
            "mid_status":slot["mid_status"] if slot else None,
            "publish_permission_status":(
                slot["publish_permission_status"] if slot else None
            ),
            "health_evidence_sha256":(
                slot["health_evidence_sha256"] if slot else None
            ),
            "last_checked_at":(
                slot["last_checked_at"].isoformat()
                if slot and slot["last_checked_at"] else None
            ),
        },
        "active_ambiguous_claim_count":ambiguous,
    }

def request_recovery(incident_id, *, actor:str)->dict:
    with engine.begin() as db:
        incident=db.execute(text("""
          SELECT * FROM shrimp_bilibili_incidents
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """),{"id":incident_id}).mappings().one_or_none()
        if incident is None:
            raise LookupError("Bilibili publisher incident not found")
        if incident["incident_status"]=="RESOLVED":
            raise RuntimeError("Resolved incident cannot request recovery")
        snapshot=_recovery_snapshot(db,dict(incident))
        circuit_id=snapshot["circuit"]["id"]
        if not circuit_id:
            raise RuntimeError("Incident account has no Circuit Breaker")
        sha=_sha256(snapshot)
        existing=db.execute(text("""
          SELECT * FROM shrimp_bilibili_recovery_approvals
          WHERE incident_id=:incident_id
            AND request_status='PENDING'
          FOR UPDATE
        """),{"incident_id":incident["id"]}).mappings().one_or_none()
        if existing is not None:
            return _serialize(existing)
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_recovery_approvals(
            incident_id,account_id,circuit_breaker_id,
            evidence_snapshot,evidence_sha256,requested_by)
          VALUES(
            :incident_id,:account_id,CAST(:circuit_id AS uuid),
            CAST(:snapshot AS jsonb),:sha,:actor)
          RETURNING *
        """),{
          "incident_id":incident["id"],
          "account_id":incident["account_id"],
          "circuit_id":circuit_id,
          "snapshot":canonical_json(snapshot),
          "sha":sha,
          "actor":actor[:200],
        }).mappings().one()
        db.execute(text("""
          UPDATE shrimp_bilibili_incidents
          SET incident_status='RECOVERY_REVIEW'
          WHERE id=:id
        """),{"id":incident["id"]})
        _timeline(
            db,
            incident_id=incident["id"],
            event_type="RECOVERY_REQUESTED",
            source_type="RECOVERY_APPROVAL",
            source_id=str(row["id"]),
            payload=snapshot,
            actor=actor,
        )
    return _serialize(row)

def decide_recovery(
    approval_id,
    *,
    decision:str,
    reason:str,
    actor:str,
)->dict:
    choice=decision.upper()
    if choice not in {"APPROVE","REJECT"}:
        raise ValueError("decision must be APPROVE or REJECT")
    with engine.begin() as db:
        approval=db.execute(text("""
          SELECT * FROM shrimp_bilibili_recovery_approvals
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """),{"id":approval_id}).mappings().one_or_none()
        if approval is None:
            raise LookupError("Recovery approval not found")
        if approval["request_status"]!="PENDING":
            raise RuntimeError("Recovery approval is not PENDING")
        incident=db.execute(text("""
          SELECT * FROM shrimp_bilibili_incidents
          WHERE id=:id FOR UPDATE
        """),{"id":approval["incident_id"]}).mappings().one()
        current=_recovery_snapshot(db,dict(incident))
        if _sha256(current)!=approval["evidence_sha256"]:
            db.execute(text("""
              UPDATE shrimp_bilibili_recovery_approvals
              SET request_status='STALE',
                  decision_reason='Evidence drifted',
                  decision_by=:actor,decided_at=now()
              WHERE id=:id
            """),{"id":approval["id"],"actor":actor[:200]})
            raise RuntimeError("Recovery evidence drifted; request a new approval")
        status="APPROVED" if choice=="APPROVE" else "REJECTED"
        updated=db.execute(text("""
          UPDATE shrimp_bilibili_recovery_approvals
          SET request_status=:status,decision=:decision,
              decision_reason=:reason,decision_by=:actor,decided_at=now()
          WHERE id=:id
          RETURNING *
        """),{
          "id":approval["id"],
          "status":status,
          "decision":choice,
          "reason":reason[:4000],
          "actor":actor[:200],
        }).mappings().one()
        _timeline(
            db,
            incident_id=approval["incident_id"],
            event_type=(
                "RECOVERY_APPROVED" if choice=="APPROVE"
                else "RECOVERY_REJECTED"
            ),
            source_type="RECOVERY_APPROVAL",
            source_id=str(approval["id"]),
            payload={
                "decision":choice,
                "reason":reason,
                "evidence_sha256":approval["evidence_sha256"],
            },
            actor=actor,
        )
    return _serialize(updated)

def apply_approved_recovery(approval_id, *, actor:str)->dict:
    with engine.begin() as db:
        approval=db.execute(text("""
          SELECT * FROM shrimp_bilibili_recovery_approvals
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """),{"id":approval_id}).mappings().one_or_none()
        if approval is None:
            raise LookupError("Recovery approval not found")
        if approval["request_status"]!="APPROVED":
            raise RuntimeError("Recovery approval is not APPROVED")
        incident=db.execute(text("""
          SELECT * FROM shrimp_bilibili_incidents
          WHERE id=:id FOR UPDATE
        """),{"id":approval["incident_id"]}).mappings().one()
        current=_recovery_snapshot(db,dict(incident))
        if _sha256(current)!=approval["evidence_sha256"]:
            db.execute(text("""
              UPDATE shrimp_bilibili_recovery_approvals
              SET request_status='STALE'
              WHERE id=:id
            """),{"id":approval["id"]})
            raise RuntimeError("Recovery evidence drifted after approval")

        slot=current["slot"]
        if (
            current["active_ambiguous_claim_count"]!=0
            or slot["slot_status"]!="ACTIVE"
            or slot["health_status"]!="HEALTHY"
            or slot["degradation_status"]!="NORMAL"
            or slot["mid_status"]!="MATCH"
            or slot["publish_permission_status"]!="ALLOWED"
        ):
            raise RuntimeError(
                "Approved recovery evidence is insufficient to close Circuit"
            )

        circuit=db.execute(text("""
          SELECT * FROM shrimp_bilibili_account_circuit_breakers
          WHERE id=:id FOR UPDATE
        """),{"id":approval["circuit_breaker_id"]}).mappings().one()
        previous=circuit["circuit_status"]
        if previous not in {"OPEN","RECOVERY_PENDING"}:
            raise RuntimeError("Circuit is not recoverable")

        recovery_evidence={
            "incident_id":str(incident["id"]),
            "approval_id":str(approval["id"]),
            "approval_evidence_sha256":approval["evidence_sha256"],
            "previous_status":previous,
            "next_status":"CLOSED",
        }
        recovery_sha=_sha256(recovery_evidence)
        db.execute(text("""
          UPDATE shrimp_bilibili_account_circuit_breakers
          SET circuit_status='CLOSED',
              closed_at=now(),recovery_not_before=NULL,
              last_evidence_type='HUMAN_APPROVED_RECOVERY',
              last_evidence_sha256=:sha,
              state_version=state_version+1,
              updated_by=:actor,updated_at=now()
          WHERE id=:id
        """),{
          "id":approval["circuit_breaker_id"],
          "sha":recovery_sha,
          "actor":actor[:200],
        })
        db.execute(text("""
          INSERT INTO shrimp_bilibili_circuit_events(
            account_id,previous_status,next_status,event_type,reason,
            evidence_payload,evidence_sha256,actor)
          VALUES(
            :account_id,:previous,'CLOSED','MANUAL_RECOVERY_REQUEST',
            'Human-approved evidence recovery',
            CAST(:payload AS jsonb),:sha,:actor)
          ON CONFLICT (evidence_sha256) DO NOTHING
        """),{
          "account_id":approval["account_id"],
          "previous":previous,
          "payload":canonical_json(recovery_evidence),
          "sha":recovery_sha,
          "actor":actor[:200],
        })
        db.execute(text("""
          UPDATE shrimp_bilibili_recovery_approvals
          SET request_status='APPLIED'
          WHERE id=:id
        """),{"id":approval["id"]})
        db.execute(text("""
          UPDATE shrimp_bilibili_incidents
          SET incident_status='RESOLVED',resolved_at=now(),
              resolution_summary='Human-approved evidence recovery applied'
          WHERE id=:id
        """),{"id":approval["incident_id"]})
        _timeline(
            db,
            incident_id=approval["incident_id"],
            event_type="RECOVERY_APPLIED",
            source_type="RECOVERY_APPROVAL",
            source_id=str(approval["id"]),
            payload=recovery_evidence,
            actor=actor,
        )
        _timeline(
            db,
            incident_id=approval["incident_id"],
            event_type="INCIDENT_RESOLVED",
            source_type="INCIDENT",
            source_id=str(approval["incident_id"]),
            payload={"resolution":"HUMAN_APPROVED_EVIDENCE_RECOVERY"},
            actor=actor,
        )
    return {
        "status":"APPLIED",
        "incident_id":str(approval["incident_id"]),
        "circuit_status":"CLOSED",
        "secrets_redacted":True,
    }

def list_recovery_approvals(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT r.*,i.incident_key,a.account_key
          FROM shrimp_bilibili_recovery_approvals r
          JOIN shrimp_bilibili_incidents i ON i.id=r.incident_id
          JOIN shrimp_bilibili_accounts a ON a.id=r.account_id
          ORDER BY r.requested_at DESC,r.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_serialize(x) for x in rows]


def queue_notification(
    *,
    incident_id,
    notification_type:str,
    severity:str,
    payload:dict,
)->dict:
    destination_type=(
        "WEBHOOK"
        if settings.shrimp_bilibili_notification_webhook_url.strip()
        else "CONTROL_CENTER"
    )
    destination_ref=(
        "configured-webhook"
        if destination_type=="WEBHOOK"
        else "publisher-operations"
    )
    material={
        "incident_id":str(incident_id) if incident_id else None,
        "notification_type":notification_type,
        "severity":severity,
        "destination_type":destination_type,
        "destination_ref":destination_ref,
        "payload":payload,
    }
    sha=_sha256(material)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_notification_outbox(
            incident_id,notification_type,severity,destination_type,
            destination_ref,payload,payload_sha256)
          VALUES(
            :incident_id,:notification_type,:severity,:destination_type,
            :destination_ref,CAST(:payload AS jsonb),:sha)
          ON CONFLICT (payload_sha256) DO NOTHING
          RETURNING *
        """),{
          "incident_id":incident_id,
          "notification_type":notification_type,
          "severity":severity,
          "destination_type":destination_type,
          "destination_ref":destination_ref,
          "payload":canonical_json(payload),
          "sha":sha,
        }).mappings().one_or_none()
        if row is None:
            row=db.execute(text("""
              SELECT * FROM shrimp_bilibili_notification_outbox
              WHERE payload_sha256=:sha
            """),{"sha":sha}).mappings().one()
    return _serialize(row)

def list_notifications(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT n.*,i.incident_key
          FROM shrimp_bilibili_notification_outbox n
          LEFT JOIN shrimp_bilibili_incidents i ON i.id=n.incident_id
          ORDER BY n.created_at DESC,n.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_serialize(x) for x in rows]


def deliver_notifications(*,actor:str)->dict:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_notification_outbox
          WHERE delivery_status IN ('QUEUED','FAILED')
            AND attempt_count<5
          ORDER BY created_at,id
          LIMIT 50
        """)).mappings().all()
    delivered=0
    failed=0
    for raw in rows:
        row=dict(raw)
        if row["destination_type"]=="CONTROL_CENTER":
            with engine.begin() as db:
                db.execute(text("""
                  UPDATE shrimp_bilibili_notification_outbox
                  SET delivery_status='DELIVERED',
                      attempt_count=attempt_count+1,
                      last_attempt_at=now(),delivered_at=now(),
                      last_error_type=NULL,last_error_sha256=NULL
                  WHERE id=:id
                """),{"id":row["id"]})
            delivered+=1
            continue
        url=settings.shrimp_bilibili_notification_webhook_url.strip()
        if not url:
            failed+=1
            continue
        headers={"Content-Type":"application/json"}
        token=settings.shrimp_bilibili_notification_webhook_token.strip()
        if token:
            headers["Authorization"]="Bearer "+token
        try:
            response=httpx.post(
                url,
                json=dict(row["payload"]),
                headers=headers,
                timeout=10.0,
            )
            response.raise_for_status()
            with engine.begin() as db:
                db.execute(text("""
                  UPDATE shrimp_bilibili_notification_outbox
                  SET delivery_status='DELIVERED',
                      attempt_count=attempt_count+1,
                      last_attempt_at=now(),delivered_at=now(),
                      last_error_type=NULL,last_error_sha256=NULL
                  WHERE id=:id
                """),{"id":row["id"]})
            delivered+=1
        except Exception as exc:
            err_sha=_sha256({
                "type":type(exc).__name__,
                "message":str(exc)[:500],
            })
            with engine.begin() as db:
                db.execute(text("""
                  UPDATE shrimp_bilibili_notification_outbox
                  SET delivery_status='FAILED',
                      attempt_count=attempt_count+1,
                      last_attempt_at=now(),
                      last_error_type=:error_type,
                      last_error_sha256=:error_sha
                  WHERE id=:id
                """),{
                  "id":row["id"],
                  "error_type":type(exc).__name__,
                  "error_sha":err_sha,
                })
            failed+=1
    return {
        "delivered":delivered,
        "failed":failed,
        "secrets_redacted":True,
    }
