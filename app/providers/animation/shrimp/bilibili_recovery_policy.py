from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_credentials import (
    credential_slot_is_fresh,
)
from app.providers.animation.shrimp.bilibili_quota_ops import (
    list_stuck_claims,
    reconcile_stuck_claim,
)

def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def _serialize(row: Any) -> dict:
    result=dict(row)
    for key in ("id","account_id","execution_claim_id","execution_id"):
        if result.get(key) is not None:
            result[key]=str(result[key])
    for key in (
        "opened_at","resolved_at","updated_at","recovery_not_before",
        "closed_at","created_at","started_at","finished_at",
    ):
        if result.get(key) is not None:
            result[key]=result[key].isoformat()
    return result

def _account_circuit_row(db, account_id, *, actor: str) -> dict:
    row=db.execute(text("""
      SELECT * FROM shrimp_bilibili_account_circuit_breakers
      WHERE account_id=:account_id
      FOR UPDATE
    """),{"account_id":account_id}).mappings().one_or_none()
    if row is not None:
        return dict(row)
    row=db.execute(text("""
      INSERT INTO shrimp_bilibili_account_circuit_breakers(
        account_id,circuit_status,updated_by)
      VALUES(:account_id,'CLOSED',:actor)
      RETURNING *
    """),{
      "account_id":account_id,
      "actor":(actor or "shrimp-circuit-init")[:200],
    }).mappings().one()
    return dict(row)

def account_circuit_allows_reservation(db, account_id) -> bool:
    row=db.execute(text("""
      SELECT circuit_status
      FROM shrimp_bilibili_account_circuit_breakers
      WHERE account_id=:account_id
    """),{"account_id":account_id}).mappings().one_or_none()
    if row is None:
        return True
    return row["circuit_status"]=="CLOSED"

def _circuit_event(
    db,
    *,
    account_id,
    previous_status: str,
    next_status: str,
    event_type: str,
    reason: str,
    evidence: dict,
    actor: str,
) -> dict:
    evidence_sha=_sha256(evidence)
    row=db.execute(text("""
      INSERT INTO shrimp_bilibili_circuit_events(
        account_id,previous_status,next_status,event_type,reason,
        evidence_payload,evidence_sha256,actor)
      VALUES(
        :account_id,:previous_status,:next_status,:event_type,:reason,
        CAST(:payload AS jsonb),:sha,:actor)
      ON CONFLICT (evidence_sha256) DO NOTHING
      RETURNING *
    """),{
      "account_id":account_id,
      "previous_status":previous_status,
      "next_status":next_status,
      "event_type":event_type,
      "reason":reason[:1000],
      "payload":canonical_json(evidence),
      "sha":evidence_sha,
      "actor":(actor or "shrimp-circuit-policy")[:200],
    }).mappings().one_or_none()
    if row is None:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_circuit_events
          WHERE evidence_sha256=:sha
        """),{"sha":evidence_sha}).mappings().one()
    return _serialize(row)

def _ambiguity_count(db, account_id) -> int:
    return int(db.execute(text("""
      SELECT COUNT(*)
      FROM shrimp_bilibili_stuck_claim_reconciliations
      WHERE account_id=:account_id
        AND reconciliation_outcome='STILL_AMBIGUOUS'
        AND reconciled_at>=now()-interval '24 hours'
    """),{"account_id":account_id}).scalar_one())

def _provider_failure_count(db, account_id) -> int:
    return int(db.execute(text("""
      SELECT COUNT(*)
      FROM shrimp_animation_publish_executions e
      JOIN shrimp_bilibili_execution_claims c ON c.execution_id=e.id
      WHERE c.account_id=:account_id
        AND e.execution_status IN ('UPLOAD_FAILED','PUBLISH_FAILED')
        AND COALESCE(
          e.publish_attempted_at,
          e.upload_attempted_at,
          e.created_at
        )>=now()-interval '24 hours'
    """),{"account_id":account_id}).scalar_one())

def _set_circuit_open(
    db,
    *,
    account_id,
    reason: str,
    ambiguity_score: int,
    provider_failure_score: int,
    event_type: str,
    actor: str,
) -> bool:
    circuit=_account_circuit_row(db,account_id,actor=actor)
    previous=circuit["circuit_status"]
    cooldown=max(1,int(settings.shrimp_bilibili_circuit_cooldown_minutes))
    if previous=="OPEN":
        db.execute(text("""
          UPDATE shrimp_bilibili_account_circuit_breakers
          SET ambiguity_score=:ambiguity,
              provider_failure_score=:failures,
              updated_by=:actor,
              updated_at=now()
          WHERE account_id=:account_id
        """),{
          "account_id":account_id,
          "ambiguity":ambiguity_score,
          "failures":provider_failure_score,
          "actor":actor[:200],
        })
        return False
    evidence={
        "schema_version":"shrimp-bilibili-circuit-v0.1",
        "account_id":str(account_id),
        "previous_status":previous,
        "next_status":"OPEN",
        "reason":reason,
        "ambiguity_score":ambiguity_score,
        "provider_failure_score":provider_failure_score,
    }
    db.execute(text("""
      UPDATE shrimp_bilibili_account_circuit_breakers
      SET circuit_status='OPEN',
          open_reason=:reason,
          ambiguity_score=:ambiguity,
          provider_failure_score=:failures,
          opened_at=now(),
          recovery_not_before=now()+(:cooldown * interval '1 minute'),
          closed_at=NULL,
          last_evidence_type=:event_type,
          last_evidence_sha256=:sha,
          state_version=state_version+1,
          updated_by=:actor,
          updated_at=now()
      WHERE account_id=:account_id
    """),{
      "account_id":account_id,
      "reason":reason[:1000],
      "ambiguity":ambiguity_score,
      "failures":provider_failure_score,
      "cooldown":cooldown,
      "event_type":event_type,
      "sha":_sha256(evidence),
      "actor":actor[:200],
    })
    _circuit_event(
      db,
      account_id=account_id,
      previous_status=previous,
      next_status="OPEN",
      event_type=event_type,
      reason=reason,
      evidence=evidence,
      actor=actor,
    )
    return True

def _recovery_evidence(db, account_id) -> dict:
    slot=db.execute(text("""
      SELECT cs.*
      FROM shrimp_bilibili_credential_slots cs
      WHERE cs.account_id=:account_id
      LIMIT 1
    """),{"account_id":account_id}).mappings().one_or_none()
    active_claims=int(db.execute(text("""
      SELECT COUNT(*)
      FROM shrimp_bilibili_execution_claims c
      JOIN shrimp_animation_publish_executions e ON e.id=c.execution_id
      WHERE c.account_id=:account_id
        AND c.claim_status='CLAIMED'
        AND e.execution_status IN (
          'UPLOADING','UPLOAD_UNKNOWN','PUBLISHING','PUBLISH_UNKNOWN'
        )
    """),{"account_id":account_id}).scalar_one())
    slot_ok=False
    slot_snapshot=None
    if slot is not None:
        slot_dict=dict(slot)
        slot_ok=(
            slot_dict["slot_status"]=="ACTIVE"
            and slot_dict["health_status"]=="HEALTHY"
            and slot_dict["degradation_status"]=="NORMAL"
            and slot_dict["mid_status"]=="MATCH"
            and slot_dict["publish_permission_status"]=="ALLOWED"
            and credential_slot_is_fresh(slot_dict)
        )
        slot_snapshot={
            "slot_key":slot_dict["slot_key"],
            "health_status":slot_dict["health_status"],
            "degradation_status":slot_dict["degradation_status"],
            "mid_status":slot_dict["mid_status"],
            "publish_permission_status":slot_dict["publish_permission_status"],
            "health_evidence_sha256":slot_dict["health_evidence_sha256"],
            "last_checked_at":(
                slot_dict["last_checked_at"].isoformat()
                if slot_dict.get("last_checked_at") else None
            ),
        }
    return {
        "slot_ok":slot_ok,
        "slot":slot_snapshot,
        "active_ambiguous_claim_count":active_claims,
    }

def evaluate_account_circuit(account_id, *, actor: str) -> dict:
    with engine.begin() as db:
        circuit=_account_circuit_row(db,account_id,actor=actor)
        ambiguity=_ambiguity_count(db,account_id)
        failures=_provider_failure_count(db,account_id)
        opened=False
        closed=False

        if ambiguity>=max(
            1,int(settings.shrimp_bilibili_circuit_ambiguity_threshold)
        ):
            opened=_set_circuit_open(
                db,
                account_id=account_id,
                reason="Repeated ambiguous provider read-back outcomes",
                ambiguity_score=ambiguity,
                provider_failure_score=failures,
                event_type="AUTO_OPEN_AMBIGUITY",
                actor=actor,
            )
        elif failures>=max(
            1,int(settings.shrimp_bilibili_circuit_provider_failure_threshold)
        ):
            opened=_set_circuit_open(
                db,
                account_id=account_id,
                reason="Repeated definitive provider failures",
                ambiguity_score=ambiguity,
                provider_failure_score=failures,
                event_type="AUTO_OPEN_PROVIDER_FAILURE",
                actor=actor,
            )

        circuit=_account_circuit_row(db,account_id,actor=actor)
        ambiguity_threshold=max(
            1,int(settings.shrimp_bilibili_circuit_ambiguity_threshold)
        )
        failure_threshold=max(
            1,int(settings.shrimp_bilibili_circuit_provider_failure_threshold)
        )
        if (
            circuit["circuit_status"] in {"OPEN","RECOVERY_PENDING"}
            and circuit.get("recovery_not_before") is not None
            and circuit["recovery_not_before"]<=datetime.now(timezone.utc)
            and ambiguity<ambiguity_threshold
            and failures<failure_threshold
        ):
            evidence=_recovery_evidence(db,account_id)
            previous=circuit["circuit_status"]
            if evidence["slot_ok"] and evidence["active_ambiguous_claim_count"]==0:
                event_payload={
                    "schema_version":"shrimp-bilibili-circuit-recovery-v0.1",
                    "account_id":str(account_id),
                    "recovery_evidence":evidence,
                    "ambiguity_score":ambiguity,
                    "provider_failure_score":failures,
                }
                event_sha=_sha256(event_payload)
                db.execute(text("""
                  UPDATE shrimp_bilibili_account_circuit_breakers
                  SET circuit_status='CLOSED',
                      ambiguity_score=:ambiguity,
                      provider_failure_score=:failures,
                      closed_at=now(),
                      recovery_not_before=NULL,
                      last_evidence_type='AUTO_CLOSE_EVIDENCE',
                      last_evidence_sha256=:sha,
                      state_version=state_version+1,
                      updated_by=:actor,
                      updated_at=now()
                  WHERE account_id=:account_id
                """),{
                  "account_id":account_id,
                  "ambiguity":ambiguity,
                  "failures":failures,
                  "sha":event_sha,
                  "actor":actor[:200],
                })
                _circuit_event(
                    db,
                    account_id=account_id,
                    previous_status=previous,
                    next_status="CLOSED",
                    event_type="AUTO_CLOSE_EVIDENCE",
                    reason="Fresh healthy slot evidence and no ambiguous Claim",
                    evidence=event_payload,
                    actor=actor,
                )
                closed=True
            else:
                if circuit["circuit_status"]!="RECOVERY_PENDING":
                    event_payload={
                        "schema_version":"shrimp-bilibili-circuit-recovery-v0.1",
                        "account_id":str(account_id),
                        "recovery_evidence":evidence,
                    }
                    db.execute(text("""
                      UPDATE shrimp_bilibili_account_circuit_breakers
                      SET circuit_status='RECOVERY_PENDING',
                          last_evidence_type='RECOVERY_PENDING',
                          last_evidence_sha256=:sha,
                          state_version=state_version+1,
                          updated_by=:actor,
                          updated_at=now()
                      WHERE account_id=:account_id
                    """),{
                      "account_id":account_id,
                      "sha":_sha256(event_payload),
                      "actor":actor[:200],
                    })
                    _circuit_event(
                        db,
                        account_id=account_id,
                        previous_status=previous,
                        next_status="RECOVERY_PENDING",
                        event_type="RECOVERY_PENDING",
                        reason="Recovery evidence is not yet sufficient",
                        evidence=event_payload,
                        actor=actor,
                    )

        final=_account_circuit_row(db,account_id,actor=actor)
    return {
        **_serialize(final),
        "opened_now":opened,
        "closed_now":closed,
    }

def _claim_escalation_level(age_minutes: int, ambiguity_count: int) -> str:
    if (
        age_minutes>=max(
            1,int(settings.shrimp_bilibili_claim_critical_minutes)
        )
        or ambiguity_count>=max(
            1,int(settings.shrimp_bilibili_circuit_ambiguity_threshold)
        )
    ):
        return "CRITICAL"
    if age_minutes>=max(
        1,int(settings.shrimp_bilibili_claim_warning_minutes)
    ) or ambiguity_count>=2:
        return "WARNING"
    return "INFO"

def sync_claim_escalations(*,actor: str) -> list[dict]:
    stuck=list_stuck_claims(limit=500)
    active_claim_ids={str(x["id"]) for x in stuck}
    results=[]
    with engine.begin() as db:
        for claim in stuck:
            claimed_at=claim["claimed_at"]
            if isinstance(claimed_at,str):
                claimed_at=datetime.fromisoformat(claimed_at)
            if claimed_at.tzinfo is None:
                claimed_at=claimed_at.replace(tzinfo=timezone.utc)
            age=max(
                0,
                int((datetime.now(timezone.utc)-claimed_at).total_seconds()/60),
            )
            ambiguity=int(db.execute(text("""
              SELECT COUNT(*)
              FROM shrimp_bilibili_stuck_claim_reconciliations
              WHERE execution_claim_id=CAST(:claim_id AS uuid)
                AND reconciliation_outcome='STILL_AMBIGUOUS'
            """),{"claim_id":claim["id"]}).scalar_one())
            level=_claim_escalation_level(age,ambiguity)
            existing=db.execute(text("""
              SELECT * FROM shrimp_bilibili_claim_escalations
              WHERE execution_claim_id=CAST(:claim_id AS uuid)
                AND escalation_status='OPEN'
              FOR UPDATE
            """),{"claim_id":claim["id"]}).mappings().one_or_none()
            payload={
                "schema_version":"shrimp-bilibili-claim-escalation-v0.1",
                "claim_id":str(claim["id"]),
                "execution_id":str(claim["execution_id"]),
                "account_id":str(claim["account_id"]),
                "level":level,
                "age_minutes":age,
                "ambiguity_count":ambiguity,
                "execution_status":claim["execution_status"],
                "recommended_action":claim["recommended_action"],
            }
            sha=_sha256(payload)
            if existing is None:
                row=db.execute(text("""
                  INSERT INTO shrimp_bilibili_claim_escalations(
                    execution_claim_id,execution_id,account_id,
                    escalation_level,detected_execution_status,
                    claim_age_minutes,ambiguity_count,recommended_action,
                    escalation_payload,escalation_sha256,opened_by)
                  VALUES(
                    CAST(:claim_id AS uuid),CAST(:execution_id AS uuid),
                    CAST(:account_id AS uuid),:level,:execution_status,
                    :age,:ambiguity,:recommended_action,
                    CAST(:payload AS jsonb),:sha,:actor)
                  RETURNING *
                """),{
                  "claim_id":claim["id"],
                  "execution_id":claim["execution_id"],
                  "account_id":claim["account_id"],
                  "level":level,
                  "execution_status":claim["execution_status"],
                  "age":age,
                  "ambiguity":ambiguity,
                  "recommended_action":claim["recommended_action"],
                  "payload":canonical_json(payload),
                  "sha":sha,
                  "actor":actor[:200],
                }).mappings().one()
                results.append(_serialize(row))
            else:
                row=db.execute(text("""
                  UPDATE shrimp_bilibili_claim_escalations
                  SET escalation_level=:level,
                      detected_execution_status=:execution_status,
                      claim_age_minutes=:age,
                      ambiguity_count=:ambiguity,
                      recommended_action=:recommended_action,
                      escalation_payload=CAST(:payload AS jsonb),
                      escalation_sha256=:sha
                  WHERE id=:id
                  RETURNING *
                """),{
                  "id":existing["id"],
                  "level":level,
                  "execution_status":claim["execution_status"],
                  "age":age,
                  "ambiguity":ambiguity,
                  "recommended_action":claim["recommended_action"],
                  "payload":canonical_json(payload),
                  "sha":sha,
                }).mappings().one()
                results.append(_serialize(row))

        open_rows=db.execute(text("""
          SELECT id,execution_claim_id
          FROM shrimp_bilibili_claim_escalations
          WHERE escalation_status='OPEN'
        """)).mappings().all()
        for row in open_rows:
            if str(row["execution_claim_id"]) not in active_claim_ids:
                db.execute(text("""
                  UPDATE shrimp_bilibili_claim_escalations
                  SET escalation_status='RESOLVED',
                      resolved_at=now(),
                      resolution_reason='Claim no longer matches stuck criteria'
                  WHERE id=:id
                """),{"id":row["id"]})
    return results

def list_claim_escalations(*,status: str|None=None,limit:int=100)->list[dict]:
    params={"limit":max(1,min(int(limit),500))}
    sql="""
      SELECT e.*,a.account_key,r.target_key
      FROM shrimp_bilibili_claim_escalations e
      JOIN shrimp_bilibili_accounts a ON a.id=e.account_id
      JOIN shrimp_bilibili_execution_claims c ON c.id=e.execution_claim_id
      JOIN shrimp_bilibili_publish_reservations r ON r.id=c.reservation_id
    """
    if status:
        sql+=" WHERE e.escalation_status=:status"
        params["status"]=status.upper()
    sql+=" ORDER BY CASE e.escalation_level WHEN 'CRITICAL' THEN 1 WHEN 'WARNING' THEN 2 ELSE 3 END,e.opened_at"
    sql+=" LIMIT :limit"
    with engine.connect() as db:
        rows=db.execute(text(sql),params).mappings().all()
    return [_serialize(x) for x in rows]

def list_circuit_breakers(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT
            c.id,
            a.id AS account_id,
            a.account_key,
            a.display_name,
            COALESCE(c.circuit_status,'CLOSED') AS circuit_status,
            c.open_reason,
            COALESCE(c.ambiguity_score,0) AS ambiguity_score,
            COALESCE(c.provider_failure_score,0) AS provider_failure_score,
            c.opened_at,
            c.recovery_not_before,
            c.closed_at,
            c.last_evidence_type,
            c.last_evidence_sha256,
            COALESCE(c.state_version,0) AS state_version,
            c.updated_at
          FROM shrimp_bilibili_accounts a
          LEFT JOIN shrimp_bilibili_account_circuit_breakers c
            ON c.account_id=a.id
          WHERE a.account_status='ACTIVE'
          ORDER BY CASE COALESCE(c.circuit_status,'CLOSED')
                     WHEN 'OPEN' THEN 1
                     WHEN 'RECOVERY_PENDING' THEN 2 ELSE 3 END,
                   c.updated_at DESC NULLS LAST,
                   a.account_key
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_serialize(x) for x in rows]

def list_circuit_events(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT e.*,a.account_key
          FROM shrimp_bilibili_circuit_events e
          JOIN shrimp_bilibili_accounts a ON a.id=e.account_id
          ORDER BY e.created_at DESC,e.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_serialize(x) for x in rows]

def evaluate_all_circuits(*,actor: str) -> dict:
    with engine.connect() as db:
        account_ids=db.execute(text("""
          SELECT id FROM shrimp_bilibili_accounts
          WHERE account_status='ACTIVE'
          ORDER BY account_key
        """)).scalars().all()
    opened=0
    closed=0
    results=[]
    for account_id in account_ids:
        result=evaluate_account_circuit(account_id,actor=actor)
        opened+=1 if result["opened_now"] else 0
        closed+=1 if result["closed_now"] else 0
        results.append(result)
    return {"opened":opened,"closed":closed,"circuits":results}

def run_recovery_policy(*,actor: str, auto_readback: bool=True) -> dict:
    clean_actor=(actor or "shrimp-recovery-policy")[:200]
    with engine.begin() as db:
        run=db.execute(text("""
          INSERT INTO shrimp_bilibili_recovery_policy_runs(
            run_status,started_by)
          VALUES('RUNNING',:actor)
          RETURNING *
        """),{"actor":clean_actor}).mappings().one()

    stuck=list_stuck_claims(limit=500)
    reconciled=0
    ambiguous=0
    for claim in stuck:
        if not auto_readback:
            continue
        if claim["recommended_action"] not in {
            "UPLOAD_READBACK","PUBLISH_READBACK"
        }:
            continue
        with engine.connect() as db:
            recent=db.execute(text("""
              SELECT 1
              FROM shrimp_bilibili_stuck_claim_reconciliations
              WHERE execution_claim_id=CAST(:claim_id AS uuid)
                AND reconciled_at>=now()-interval '15 minutes'
              LIMIT 1
            """),{"claim_id":claim["id"]}).scalar_one_or_none()
        if recent:
            continue
        try:
            result=reconcile_stuck_claim(
                claim["execution_id"],
                actor=clean_actor+"-readback",
            )
            reconciled+=1
            if result["reconciliation_outcome"]=="STILL_AMBIGUOUS":
                ambiguous+=1
        except Exception:
            ambiguous+=1

    escalations=sync_claim_escalations(actor=clean_actor+"-escalation")
    circuit_result=evaluate_all_circuits(actor=clean_actor+"-circuit")
    summary={
        "stuck_claim_count":len(stuck),
        "reconciled_count":reconciled,
        "still_ambiguous_count":ambiguous,
        "escalation_count":len(escalations),
        "circuits_opened":circuit_result["opened"],
        "circuits_closed":circuit_result["closed"],
    }
    summary_sha=_sha256(summary)
    status="SUCCEEDED" if ambiguous==0 else "PARTIAL"
    with engine.begin() as db:
        final=db.execute(text("""
          UPDATE shrimp_bilibili_recovery_policy_runs
          SET run_status=:status,
              stuck_claim_count=:stuck,
              reconciled_count=:reconciled,
              still_ambiguous_count=:ambiguous,
              escalation_count=:escalations,
              circuits_opened=:opened,
              circuits_closed=:closed,
              summary_payload=CAST(:payload AS jsonb),
              summary_sha256=:sha,
              finished_at=now()
          WHERE id=:id
          RETURNING *
        """),{
          "id":run["id"],
          "status":status,
          "stuck":summary["stuck_claim_count"],
          "reconciled":reconciled,
          "ambiguous":ambiguous,
          "escalations":len(escalations),
          "opened":circuit_result["opened"],
          "closed":circuit_result["closed"],
          "payload":canonical_json(summary),
          "sha":summary_sha,
        }).mappings().one()
    result=_serialize(final)
    result["summary"]=summary
    result["secrets_redacted"]=True
    return result

def list_recovery_policy_runs(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT * FROM shrimp_bilibili_recovery_policy_runs
          ORDER BY started_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_serialize(x) for x in rows]

def operations_console() -> dict:
    escalations=list_claim_escalations(status="OPEN",limit=100)
    circuits=list_circuit_breakers(limit=100)
    runs=list_recovery_policy_runs(limit=20)
    return {
        "open_escalations":escalations,
        "circuits":circuits,
        "recovery_runs":runs,
        "summary":{
            "critical_escalations":sum(
                1 for x in escalations if x["escalation_level"]=="CRITICAL"
            ),
            "warning_escalations":sum(
                1 for x in escalations if x["escalation_level"]=="WARNING"
            ),
            "open_circuits":sum(
                1 for x in circuits if x["circuit_status"]=="OPEN"
            ),
            "recovery_pending":sum(
                1 for x in circuits
                if x["circuit_status"]=="RECOVERY_PENDING"
            ),
        },
        "secrets_redacted":True,
    }
