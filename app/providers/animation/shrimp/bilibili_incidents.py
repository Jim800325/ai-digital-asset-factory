from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import text

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
