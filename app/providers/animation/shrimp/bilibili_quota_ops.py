from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_quota import (
    get_quota_usage,
)
from app.providers.animation.shrimp.publisher_execution import (
    get_publish_execution,
    reconcile_publish_upload,
    reconcile_published_media,
)

def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def _serialize(row: Any) -> dict:
    result=dict(row)
    for key in ("id","account_id","execution_claim_id","execution_id"):
        if result.get(key) is not None:
            result[key]=str(result[key])
    for key in ("reconciled_at","audited_at","claimed_at","created_at"):
        if result.get(key) is not None:
            result[key]=result[key].isoformat()
    return result

def list_stuck_claims(*,limit:int=100)->list[dict]:
    threshold=max(1,int(settings.shrimp_bilibili_stuck_claim_minutes))
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT c.*,a.account_key,r.target_key,
                 e.execution_status,e.upload_outcome,e.publish_outcome,
                 e.upload_write_count,e.publish_write_count,
                 e.provider_upload_id,e.provider_publish_id,
                 e.publish_attempted_at,e.created_at AS execution_created_at
          FROM shrimp_bilibili_execution_claims c
          JOIN shrimp_bilibili_accounts a ON a.id=c.account_id
          JOIN shrimp_bilibili_publish_reservations r ON r.id=c.reservation_id
          JOIN shrimp_animation_publish_executions e ON e.id=c.execution_id
          WHERE c.claim_status='CLAIMED'
            AND (
              c.claimed_at <= now() - (:threshold * interval '1 minute')
              OR e.execution_status IN (
                'UPLOAD_UNKNOWN','PUBLISH_UNKNOWN','UPLOADING','PUBLISHING'
              )
            )
          ORDER BY c.claimed_at ASC,c.id
          LIMIT :limit
        """),{
          "threshold":threshold,
          "limit":max(1,min(int(limit),500)),
        }).mappings().all()
    result=[]
    for row in rows:
        item=_serialize(row)
        status=str(item.get("execution_status") or "")
        if status in {"PUBLISH_UNKNOWN","PUBLISHING"}:
            item["recommended_action"]="PUBLISH_READBACK"
        elif status in {"UPLOAD_UNKNOWN","UPLOADING"}:
            item["recommended_action"]="UPLOAD_READBACK"
        else:
            item["recommended_action"]="MANUAL_REVIEW_REQUIRED"
        item["stuck_threshold_minutes"]=threshold
        result.append(item)
    return result

def reconcile_stuck_claim(
    execution_id,
    *,
    actor:str,
)->dict:
    before=get_publish_execution(execution_id)
    with engine.connect() as db:
        claim=db.execute(text("""
          SELECT c.*,a.account_key
          FROM shrimp_bilibili_execution_claims c
          JOIN shrimp_bilibili_accounts a ON a.id=c.account_id
          WHERE c.execution_id=CAST(:execution_id AS uuid)
        """),{"execution_id":execution_id}).mappings().one_or_none()
    if claim is None:
        raise LookupError("Bilibili execution claim not found")
    if claim["claim_status"]!="CLAIMED":
        raise RuntimeError("Only CLAIMED executions require stuck reconciliation")

    status=str(before.get("execution_status") or "")
    action="MANUAL_REVIEW_REQUIRED"
    outcome="BLOCKED"
    error_type=None
    if status in {"UPLOAD_UNKNOWN","UPLOADING"}:
        action="UPLOAD_READBACK"
        try:
            reconcile_publish_upload(execution_id,actor=actor)
            outcome="RECONCILED"
        except RuntimeError as exc:
            error_type=type(exc).__name__
            after=get_publish_execution(execution_id)
            if after["execution_status"]=="UPLOAD_UNKNOWN":
                outcome="STILL_AMBIGUOUS"
            else:
                outcome="FAILED"
    elif status in {"PUBLISH_UNKNOWN","PUBLISHING"}:
        action="PUBLISH_READBACK"
        try:
            reconcile_published_media(execution_id,actor=actor)
            outcome="RECONCILED"
        except RuntimeError as exc:
            error_type=type(exc).__name__
            after=get_publish_execution(execution_id)
            if after["execution_status"]=="PUBLISH_UNKNOWN":
                outcome="STILL_AMBIGUOUS"
            else:
                outcome="FAILED"
    elif status in {"PUBLISHED","UPLOAD_FAILED","PUBLISH_FAILED"}:
        action="NONE_REQUIRED"
        outcome="NO_ACTION"
    else:
        action="MANUAL_REVIEW_REQUIRED"
        outcome="BLOCKED"

    after=get_publish_execution(execution_id)
    with engine.connect() as db:
        refreshed=db.execute(text("""
          SELECT * FROM shrimp_bilibili_execution_claims
          WHERE execution_id=CAST(:execution_id AS uuid)
        """),{"execution_id":execution_id}).mappings().one()
    evidence={
        "schema_version":"shrimp-bilibili-stuck-claim-reconciliation-v0.1",
        "execution_id":str(execution_id),
        "claim_id":str(refreshed["id"]),
        "account_id":str(refreshed["account_id"]),
        "execution_status_before":before["execution_status"],
        "execution_status_after":after["execution_status"],
        "action":action,
        "outcome":outcome,
        "claim_status_after":refreshed["claim_status"],
        "upload_write_count":after.get("upload_write_count"),
        "publish_write_count":after.get("publish_write_count"),
        "error_type":error_type,
        "provider_write_performed":False,
    }
    evidence_sha=_sha256(evidence)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_stuck_claim_reconciliations(
            execution_claim_id,execution_id,account_id,
            detected_status,execution_status_before,execution_status_after,
            reconciliation_action,reconciliation_outcome,
            provider_write_performed,claim_status_after,
            evidence_payload,evidence_sha256,reconciled_by)
          VALUES(
            :claim_id,:execution_id,:account_id,
            :detected_status,:before_status,:after_status,
            :action,:outcome,false,:claim_status_after,
            CAST(:payload AS jsonb),:evidence_sha,:actor)
          ON CONFLICT (evidence_sha256) DO NOTHING
          RETURNING *
        """),{
          "claim_id":refreshed["id"],
          "execution_id":execution_id,
          "account_id":refreshed["account_id"],
          "detected_status":status,
          "before_status":before["execution_status"],
          "after_status":after["execution_status"],
          "action":action,
          "outcome":outcome,
          "claim_status_after":refreshed["claim_status"],
          "payload":canonical_json(evidence),
          "evidence_sha":evidence_sha,
          "actor":(actor or "shrimp-stuck-reconcile")[:200],
        }).mappings().one_or_none()
        if row is None:
            row=db.execute(text("""
              SELECT * FROM shrimp_bilibili_stuck_claim_reconciliations
              WHERE evidence_sha256=:sha
            """),{"sha":evidence_sha}).mappings().one()
    result=_serialize(row)
    result["evidence"]=evidence
    return result

def list_stuck_reconciliations(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT r.*,a.account_key
          FROM shrimp_bilibili_stuck_claim_reconciliations r
          JOIN shrimp_bilibili_accounts a ON a.id=r.account_id
          ORDER BY r.reconciled_at DESC,r.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_serialize(x) for x in rows]

def _previous_day_published_units(db, account_id, local_date):
    return int(db.execute(text("""
      SELECT COALESCE(SUM(quota_units),0)
      FROM shrimp_bilibili_quota_ledger
      WHERE account_id=:account_id
        AND local_quota_date=:local_date
        AND entry_type='PUBLISH_COMMITTED'
    """),{
      "account_id":account_id,
      "local_date":local_date-timedelta(days=1),
    }).scalar_one())

def run_daily_quota_audit(*,actor:str)->list[dict]:
    with engine.connect() as db:
        accounts=db.execute(text("""
          SELECT id,account_key,timezone,daily_publish_limit
          FROM shrimp_bilibili_accounts
          WHERE account_status='ACTIVE'
          ORDER BY account_key
        """)).mappings().all()
    results=[]
    for raw in accounts:
        account=dict(raw)
        usage=get_quota_usage(account["account_key"])
        tz=ZoneInfo(str(account["timezone"] or "Asia/Shanghai"))
        local_date=datetime.now(timezone.utc).astimezone(tz).date()
        with engine.connect() as db:
            previous=_previous_day_published_units(db,account["id"],local_date)
            carry_claims=int(db.execute(text("""
              SELECT COUNT(*)
              FROM shrimp_bilibili_execution_claims
              WHERE account_id=:account_id
                AND claim_status='CLAIMED'
                AND claimed_at < :day_start
            """),{
              "account_id":account["id"],
              "day_start":datetime.combine(
                local_date,
                datetime.min.time(),
                tzinfo=tz,
              ).astimezone(timezone.utc),
            }).scalar_one())
            carry_reservations=int(db.execute(text("""
              SELECT COUNT(*)
              FROM shrimp_bilibili_publish_reservations
              WHERE account_id=:account_id
                AND reservation_status IN ('HELD','CONSUMED')
                AND created_at < :day_start
            """),{
              "account_id":account["id"],
              "day_start":datetime.combine(
                local_date,
                datetime.min.time(),
                tzinfo=tz,
              ).astimezone(timezone.utc),
            }).scalar_one())

        status="BALANCED"
        if usage["published_units"]>usage["daily_publish_limit"]:
            status="OVER_LIMIT"
        elif carry_claims or carry_reservations:
            status="CARRYOVER_PRESENT"
        expected=max(
            0,
            usage["daily_publish_limit"]
            -usage["published_units"]
            -usage["held_units"]
            -usage["claimed_units"],
        )
        if expected!=usage["available_units"]:
            status="INCONSISTENT"

        payload={
            "schema_version":"shrimp-bilibili-daily-quota-audit-v0.1",
            "account_key":account["account_key"],
            "local_quota_date":str(local_date),
            "account_timezone":account["timezone"],
            "daily_publish_limit":usage["daily_publish_limit"],
            "published_units":usage["published_units"],
            "held_units":usage["held_units"],
            "claimed_units":usage["claimed_units"],
            "available_units":usage["available_units"],
            "carryover_claim_count":carry_claims,
            "carryover_reservation_count":carry_reservations,
            "previous_day_published_units":previous,
            "audit_status":status,
        }
        sha=_sha256(payload)
        with engine.begin() as db:
            row=db.execute(text("""
              INSERT INTO shrimp_bilibili_daily_quota_audits(
                account_id,local_quota_date,account_timezone,
                daily_publish_limit,published_units,held_units,claimed_units,
                available_units,carryover_claim_count,
                carryover_reservation_count,previous_day_published_units,
                audit_status,audit_payload,audit_sha256,audited_by)
              VALUES(
                :account_id,:local_date,:timezone,
                :daily_limit,:published,:held,:claimed,
                :available,:carry_claims,:carry_reservations,:previous,
                :status,CAST(:payload AS jsonb),:sha,:actor)
              ON CONFLICT (account_id,local_quota_date) DO NOTHING
              RETURNING *
            """),{
              "account_id":account["id"],
              "local_date":local_date,
              "timezone":account["timezone"],
              "daily_limit":usage["daily_publish_limit"],
              "published":usage["published_units"],
              "held":usage["held_units"],
              "claimed":usage["claimed_units"],
              "available":usage["available_units"],
              "carry_claims":carry_claims,
              "carry_reservations":carry_reservations,
              "previous":previous,
              "status":status,
              "payload":canonical_json(payload),
              "sha":sha,
              "actor":(actor or "shrimp-daily-quota-audit")[:200],
            }).mappings().one_or_none()
            if row is None:
                row=db.execute(text("""
                  SELECT * FROM shrimp_bilibili_daily_quota_audits
                  WHERE account_id=:account_id
                    AND local_quota_date=:local_date
                """),{
                  "account_id":account["id"],
                  "local_date":local_date,
                }).mappings().one()
        results.append(_serialize(row))
    return results

def list_daily_quota_audits(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT q.*,a.account_key
          FROM shrimp_bilibili_daily_quota_audits q
          JOIN shrimp_bilibili_accounts a ON a.id=q.account_id
          ORDER BY q.local_quota_date DESC,a.account_key
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_serialize(x) for x in rows]

def quota_dashboard()->dict:
    with engine.connect() as db:
        accounts=db.execute(text("""
          SELECT account_key FROM shrimp_bilibili_accounts
          WHERE account_status='ACTIVE'
          ORDER BY account_key
        """)).scalars().all()
    usage=[]
    for account_key in accounts:
        usage.append(get_quota_usage(account_key))
    claims=list_stuck_claims(limit=100)
    return {
        "accounts":usage,
        "stuck_claims":claims,
        "reservations_summary":{
            "held":sum(x["held_units"] for x in usage),
            "claimed":sum(x["claimed_units"] for x in usage),
            "published_today":sum(x["published_units"] for x in usage),
            "available":sum(x["available_units"] for x in usage),
        },
        "stuck_claim_count":len(claims),
        "stuck_threshold_minutes":max(
            1,int(settings.shrimp_bilibili_stuck_claim_minutes)
        ),
        "secrets_redacted":True,
    }
