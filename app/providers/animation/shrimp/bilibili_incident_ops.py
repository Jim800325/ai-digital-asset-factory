from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_incidents import queue_notification

def _sha(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def _ser(row: Any) -> dict:
    out=dict(row)
    for k in ("id","incident_id","pir_id","account_id"):
        if out.get(k) is not None:
            out[k]=str(out[k])
    for k in (
        "opened_at","acknowledged_at","owner_assigned_at","ack_due_at",
        "recovery_due_at","resolved_at","created_at","updated_at",
        "completed_at","due_at",
    ):
        if out.get(k) is not None:
            out[k]=out[k].isoformat()
    return out

def _timeline(db, incident_id, event_type, payload, actor):
    material={
        "incident_id":str(incident_id),
        "event_type":event_type,
        "payload":payload,
    }
    sha=_sha(material)
    db.execute(text("""
      INSERT INTO shrimp_bilibili_incident_timeline(
        incident_id,event_type,source_type,source_id,
        event_payload,event_sha256,actor)
      VALUES(
        :incident_id,:event_type,'INCIDENT_OPS',NULL,
        CAST(:payload AS jsonb),:sha,:actor)
      ON CONFLICT (event_sha256) DO NOTHING
    """),{
      "incident_id":incident_id,
      "event_type":event_type,
      "payload":canonical_json(payload),
      "sha":sha,
      "actor":actor[:200],
    })

def _active_route(db, severity):
    return db.execute(text("""
      SELECT * FROM shrimp_bilibili_oncall_routes
      WHERE severity=:severity AND route_status='ACTIVE'
      ORDER BY created_at DESC
      LIMIT 1
    """),{"severity":severity}).mappings().one_or_none()

def ensure_default_oncall_routes(*,actor:str)->list[dict]:
    result=[]
    with engine.begin() as db:
        for severity in ("WARNING","CRITICAL"):
            existing=_active_route(db,severity)
            if existing is None:
                row=db.execute(text("""
                  INSERT INTO shrimp_bilibili_oncall_routes(
                    route_key,severity,owner_ref,secondary_owner_ref,
                    route_status,metadata,created_by)
                  VALUES(
                    :key,:severity,:owner,:secondary,'ACTIVE',
                    CAST(:metadata AS jsonb),:actor)
                  RETURNING *
                """),{
                  "key":"bilibili-"+severity.lower()+"-default",
                  "severity":severity,
                  "owner":settings.shrimp_bilibili_incident_default_owner,
                  "secondary":settings.shrimp_bilibili_incident_secondary_owner,
                  "metadata":canonical_json({"source":"default-config"}),
                  "actor":actor[:200],
                }).mappings().one()
                result.append(_ser(row))
            else:
                result.append(_ser(existing))
    return result

def bootstrap_incident_operations(*,actor:str)->list[dict]:
    ensure_default_oncall_routes(actor=actor)
    results=[]
    now=datetime.now(timezone.utc)
    ack_delta=timedelta(minutes=max(1,int(settings.shrimp_bilibili_incident_ack_sla_minutes)))
    recovery_delta=timedelta(minutes=max(1,int(settings.shrimp_bilibili_incident_recovery_sla_minutes)))
    with engine.begin() as db:
        incidents=db.execute(text("""
          SELECT * FROM shrimp_bilibili_incidents
          WHERE incident_status IN ('OPEN','RECOVERY_REVIEW')
          ORDER BY opened_at
          FOR UPDATE
        """)).mappings().all()
        for inc in incidents:
            route=_active_route(db,inc["severity"])
            owner=(route["owner_ref"] if route else settings.shrimp_bilibili_incident_default_owner)
            updates={}
            if inc["owner_ref"] is None:
                updates["owner_ref"]=owner
                updates["owner_assigned_at"]=now
            if inc["ack_due_at"] is None:
                updates["ack_due_at"]=inc["opened_at"]+ack_delta
            if inc["recovery_due_at"] is None:
                updates["recovery_due_at"]=inc["opened_at"]+recovery_delta
            if updates:
                row=db.execute(text("""
                  UPDATE shrimp_bilibili_incidents
                  SET owner_ref=COALESCE(owner_ref,:owner),
                      owner_assigned_at=COALESCE(owner_assigned_at,:owner_at),
                      ack_due_at=COALESCE(ack_due_at,:ack_due),
                      recovery_due_at=COALESCE(recovery_due_at,:recovery_due)
                  WHERE id=:id
                  RETURNING *
                """),{
                  "id":inc["id"],
                  "owner":updates.get("owner_ref"),
                  "owner_at":updates.get("owner_assigned_at"),
                  "ack_due":updates.get("ack_due_at"),
                  "recovery_due":updates.get("recovery_due_at"),
                }).mappings().one()
                if inc["owner_ref"] is None:
                    _timeline(db,inc["id"],"OWNER_ASSIGNED",{"owner_ref":owner},actor)
                results.append(_ser(row))
    return results

def acknowledge_incident(incident_id, *,actor:str)->dict:
    with engine.begin() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_incidents
          WHERE id=CAST(:id AS uuid) FOR UPDATE
        """),{"id":incident_id}).mappings().one_or_none()
        if row is None:
            raise LookupError("Incident not found")
        if row["incident_status"]=="RESOLVED":
            raise RuntimeError("Resolved incident cannot be acknowledged")
        if row["acknowledgement_status"]=="ACKNOWLEDGED":
            return _ser(row)
        updated=db.execute(text("""
          UPDATE shrimp_bilibili_incidents
          SET acknowledgement_status='ACKNOWLEDGED',
              acknowledged_by=:actor,acknowledged_at=now()
          WHERE id=:id RETURNING *
        """),{"id":row["id"],"actor":actor[:200]}).mappings().one()
        _timeline(db,row["id"],"INCIDENT_ACKNOWLEDGED",{"acknowledged_by":actor},actor)
    return _ser(updated)

def assign_incident_owner(incident_id, *,owner_ref:str,actor:str)->dict:
    clean=owner_ref.strip()
    if not clean:
        raise ValueError("owner_ref is required")
    with engine.begin() as db:
        row=db.execute(text("""
          UPDATE shrimp_bilibili_incidents
          SET owner_ref=:owner,owner_assigned_at=now()
          WHERE id=CAST(:id AS uuid)
          RETURNING *
        """),{"id":incident_id,"owner":clean[:200]}).mappings().one_or_none()
        if row is None:
            raise LookupError("Incident not found")
        _timeline(db,row["id"],"OWNER_ASSIGNED",{"owner_ref":clean},actor)
    return _ser(row)

def evaluate_incident_slas(*,actor:str)->dict:
    bootstrap_incident_operations(actor=actor+"-bootstrap")
    breached_ack=0
    breached_recovery=0
    pir_created=0
    with engine.begin() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_incidents
          ORDER BY opened_at
          FOR UPDATE
        """)).mappings().all()
        now=datetime.now(timezone.utc)
        for inc in rows:
            next_status=inc["sla_status"]
            event_type=None
            due_at=None
            if (
                inc["incident_status"]!="RESOLVED"
                and inc["acknowledgement_status"]=="UNACKNOWLEDGED"
                and inc["ack_due_at"] is not None
                and inc["ack_due_at"]<now
            ):
                next_status="ACK_BREACHED"
                event_type="ACK_BREACHED"
                due_at=inc["ack_due_at"]
                breached_ack+=1
            if (
                inc["incident_status"]!="RESOLVED"
                and inc["recovery_due_at"] is not None
                and inc["recovery_due_at"]<now
            ):
                next_status="RECOVERY_BREACHED"
                event_type="RECOVERY_BREACHED"
                due_at=inc["recovery_due_at"]
                breached_recovery+=1
            if inc["incident_status"]=="RESOLVED":
                next_status="RECOVERED"
                if inc["severity"]=="CRITICAL" and inc["pir_status"]=="NOT_REQUIRED":
                    db.execute(text("""
                      UPDATE shrimp_bilibili_incidents
                      SET pir_status='REQUIRED'
                      WHERE id=:id
                    """),{"id":inc["id"]})
                    existing=db.execute(text("""
                      SELECT id FROM shrimp_bilibili_post_incident_reviews
                      WHERE incident_id=:id
                    """),{"id":inc["id"]}).scalar_one_or_none()
                    if existing is None:
                        db.execute(text("""
                          INSERT INTO shrimp_bilibili_post_incident_reviews(
                            incident_id,review_status,created_by)
                          VALUES(:id,'DRAFT',:actor)
                        """),{"id":inc["id"],"actor":actor[:200]})
                        pir_created+=1
            if next_status!=inc["sla_status"]:
                payload={
                  "incident_id":str(inc["id"]),
                  "previous_status":inc["sla_status"],
                  "next_status":next_status,
                  "due_at":due_at.isoformat() if due_at else None,
                }
                sha=_sha(payload)
                db.execute(text("""
                  INSERT INTO shrimp_bilibili_incident_sla_events(
                    incident_id,event_type,previous_status,next_status,
                    due_at,event_payload,event_sha256,actor)
                  VALUES(
                    :incident_id,:event_type,:previous,:next,:due_at,
                    CAST(:payload AS jsonb),:sha,:actor)
                  ON CONFLICT (event_sha256) DO NOTHING
                """),{
                  "incident_id":inc["id"],
                  "event_type":event_type or "SLA_RECOVERED",
                  "previous":inc["sla_status"],
                  "next":next_status,
                  "due_at":due_at,
                  "payload":canonical_json(payload),
                  "sha":sha,
                  "actor":actor[:200],
                })
                db.execute(text("""
                  UPDATE shrimp_bilibili_incidents
                  SET sla_status=:status
                  WHERE id=:id
                """),{"id":inc["id"],"status":next_status})
                if event_type=="ACK_BREACHED":
                    _timeline(db,inc["id"],"ACK_SLA_BREACHED",payload,actor)
                elif event_type=="RECOVERY_BREACHED":
                    _timeline(db,inc["id"],"RECOVERY_SLA_BREACHED",payload,actor)
        breached=db.execute(text("""
          SELECT id,incident_key,owner_ref,sla_status,severity
          FROM shrimp_bilibili_incidents
          WHERE sla_status IN ('ACK_BREACHED','RECOVERY_BREACHED')
            AND incident_status<>'RESOLVED'
        """)).mappings().all()
    for item in breached:
        queue_notification(
            incident_id=item["id"],
            notification_type="RECOVERY_REQUIRED",
            severity="CRITICAL" if item["severity"]=="CRITICAL" else "WARNING",
            payload={
              "incident_key":item["incident_key"],
              "owner_ref":item["owner_ref"],
              "sla_status":item["sla_status"],
              "operations_path":"/animation/operations",
            },
        )
    return {
      "ack_breaches":breached_ack,
      "recovery_breaches":breached_recovery,
      "pir_created":pir_created,
    }

def list_incident_sla_events(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT s.*,i.incident_key,i.owner_ref
          FROM shrimp_bilibili_incident_sla_events s
          JOIN shrimp_bilibili_incidents i ON i.id=s.incident_id
          ORDER BY s.observed_at DESC,s.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(limit,500))}).mappings().all()
    return [_ser(x) for x in rows]

def list_post_incident_reviews(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT p.*,i.incident_key,i.owner_ref
          FROM shrimp_bilibili_post_incident_reviews p
          JOIN shrimp_bilibili_incidents i ON i.id=p.incident_id
          ORDER BY p.created_at DESC,p.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(limit,500))}).mappings().all()
    return [_ser(x) for x in rows]

def update_pir(incident_id, *,root_cause:str,lessons_learned:str,actor:str)->dict:
    with engine.begin() as db:
        pir=db.execute(text("""
          SELECT * FROM shrimp_bilibili_post_incident_reviews
          WHERE incident_id=CAST(:id AS uuid) FOR UPDATE
        """),{"id":incident_id}).mappings().one_or_none()
        if pir is None:
            raise LookupError("Post-incident review not found")
        payload={
          "root_cause":root_cause.strip(),
          "lessons_learned":lessons_learned.strip(),
        }
        sha=_sha(payload)
        row=db.execute(text("""
          UPDATE shrimp_bilibili_post_incident_reviews
          SET review_status='COMPLETED',root_cause=:root,
              lessons_learned=:lessons,review_sha256=:sha,
              completed_by=:actor,completed_at=now()
          WHERE id=:id RETURNING *
        """),{
          "id":pir["id"],"root":payload["root_cause"],
          "lessons":payload["lessons_learned"],"sha":sha,
          "actor":actor[:200],
        }).mappings().one()
        db.execute(text("""
          UPDATE shrimp_bilibili_incidents
          SET pir_status='COMPLETED'
          WHERE id=CAST(:incident_id AS uuid)
        """),{"incident_id":incident_id})
        _timeline(db,incident_id,"PIR_COMPLETED",{"review_sha256":sha},actor)
    return _ser(row)

def add_corrective_action(incident_id, *,description:str,owner_ref:str,due_at,actor:str)->dict:
    with engine.begin() as db:
        pir=db.execute(text("""
          SELECT id FROM shrimp_bilibili_post_incident_reviews
          WHERE incident_id=CAST(:id AS uuid)
        """),{"id":incident_id}).scalar_one_or_none()
        if pir is None:
            raise LookupError("Post-incident review not found")
        count=int(db.execute(text("""
          SELECT COUNT(*) FROM shrimp_bilibili_corrective_actions
          WHERE pir_id=:pir_id
        """),{"pir_id":pir}).scalar_one())
        key=f"CA-{count+1:03d}"
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_corrective_actions(
            pir_id,incident_id,action_key,description,owner_ref,due_at)
          VALUES(:pir,:incident,:key,:description,:owner,:due_at)
          RETURNING *
        """),{
          "pir":pir,"incident":incident_id,"key":key,
          "description":description.strip(),"owner":owner_ref.strip(),
          "due_at":due_at,
        }).mappings().one()
        _timeline(db,incident_id,"CORRECTIVE_ACTION_ADDED",{"action_key":key},actor)
    return _ser(row)

def list_corrective_actions(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT c.*,i.incident_key
          FROM shrimp_bilibili_corrective_actions c
          JOIN shrimp_bilibili_incidents i ON i.id=c.incident_id
          ORDER BY c.created_at DESC,c.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(limit,500))}).mappings().all()
    return [_ser(x) for x in rows]

def incident_ops_summary()->dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT
            COUNT(*) FILTER (WHERE incident_status<>'RESOLVED') AS active_incidents,
            COUNT(*) FILTER (
              WHERE incident_status<>'RESOLVED'
                AND acknowledgement_status='UNACKNOWLEDGED'
            ) AS unacknowledged,
            COUNT(*) FILTER (
              WHERE sla_status IN ('ACK_BREACHED','RECOVERY_BREACHED')
                AND incident_status<>'RESOLVED'
            ) AS sla_breached,
            COUNT(*) FILTER (WHERE pir_status='REQUIRED') AS pir_required
          FROM shrimp_bilibili_incidents
        """)).mappings().one()
    return dict(row)


def list_oncall_routes(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_oncall_routes
          ORDER BY CASE route_status WHEN 'ACTIVE' THEN 1 ELSE 2 END,
                   severity,created_at DESC
          LIMIT :limit
        """),{"limit":max(1,min(limit,500))}).mappings().all()
    return [_ser(x) for x in rows]


def configure_oncall_route(
    *,
    severity:str,
    owner_ref:str,
    secondary_owner_ref:str|None,
    actor:str,
)->dict:
    level=severity.strip().upper()
    if level not in {"WARNING","CRITICAL"}:
        raise ValueError("severity must be WARNING or CRITICAL")
    owner=owner_ref.strip()
    if not owner:
        raise ValueError("owner_ref is required")
    secondary=(secondary_owner_ref or "").strip() or None
    with engine.begin() as db:
        current=_active_route(db,level)
        if current is None:
            row=db.execute(text("""
              INSERT INTO shrimp_bilibili_oncall_routes(
                route_key,severity,owner_ref,secondary_owner_ref,
                route_status,metadata,created_by)
              VALUES(
                :key,:severity,:owner,:secondary,'ACTIVE',
                CAST(:metadata AS jsonb),:actor)
              RETURNING *
            """),{
              "key":"bilibili-"+level.lower()+"-managed",
              "severity":level,
              "owner":owner[:200],
              "secondary":secondary[:200] if secondary else None,
              "metadata":canonical_json({"source":"incident-ops-api"}),
              "actor":actor[:200],
            }).mappings().one()
        else:
            row=db.execute(text("""
              UPDATE shrimp_bilibili_oncall_routes
              SET owner_ref=:owner,
                  secondary_owner_ref=:secondary,
                  metadata=CAST(:metadata AS jsonb),
                  updated_at=now()
              WHERE id=:id
              RETURNING *
            """),{
              "id":current["id"],
              "owner":owner[:200],
              "secondary":secondary[:200] if secondary else None,
              "metadata":canonical_json({
                "source":"incident-ops-api",
                "updated_by":actor[:200],
              }),
            }).mappings().one()
    return _ser(row)

def complete_corrective_action(
    action_id,
    *,
    completion_evidence:str,
    actor:str,
)->dict:
    evidence=completion_evidence.strip()
    if len(evidence)<3:
        raise ValueError("completion_evidence is required")
    with engine.begin() as db:
        action=db.execute(text("""
          SELECT * FROM shrimp_bilibili_corrective_actions
          WHERE id=CAST(:id AS uuid)
          FOR UPDATE
        """),{"id":action_id}).mappings().one_or_none()
        if action is None:
            raise LookupError("Corrective action not found")
        if action["action_status"]=="COMPLETED":
            return _ser(action)
        row=db.execute(text("""
          UPDATE shrimp_bilibili_corrective_actions
          SET action_status='COMPLETED',
              completion_evidence=:evidence,
              completed_at=now()
          WHERE id=:id
          RETURNING *
        """),{
          "id":action["id"],
          "evidence":evidence[:4000],
        }).mappings().one()
        _timeline(
            db,
            action["incident_id"],
            "CORRECTIVE_ACTION_COMPLETED",
            {
              "action_key":action["action_key"],
              "completed_by":actor[:200],
            },
            actor,
        )
    return _ser(row)
