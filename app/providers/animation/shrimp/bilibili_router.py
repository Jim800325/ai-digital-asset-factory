from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_credentials import (
    credential_slot_is_fresh,
    select_failover_sacrificial_accounts,
)

def _sha256(value:Any)->str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def _serialize(row:Any)->dict:
    result=dict(row)
    for key in ("id","provider_job_id","account_id","credential_slot_id","target_id","consumed_by_plan_id"):
        if result.get(key) is not None:
            result[key]=str(result[key])
    for key in ("expires_at","created_at","consumed_at","released_at"):
        if result.get(key) is not None:
            result[key]=result[key].isoformat()
    return result

def expire_reservations()->int:
    with engine.begin() as db:
        return int(db.execute(text(
            "SELECT expire_shrimp_bilibili_reservations()"
        )).scalar_one())

def _target_for_account(db, account_key:str, mid:str)->dict|None:
    row=db.execute(text("""
      SELECT *
      FROM shrimp_animation_publish_targets
      WHERE platform='BILIBILI'
        AND target_status='ACTIVE'
        AND (
          account_reference=:account_key
          OR account_reference=:mid
          OR account_reference=:mid_ref
        )
      ORDER BY created_at,id
      LIMIT 1
    """),{
      "account_key":account_key,
      "mid":mid,
      "mid_ref":"MID:"+mid,
    }).mappings().one_or_none()
    return dict(row) if row else None

def _account_quota_available(db, account:dict)->bool:
    limit=int(account.get("daily_publish_limit") or 0)
    if limit<=0:
        return False
    timezone_name=str(account.get("timezone") or "Asia/Shanghai")
    from zoneinfo import ZoneInfo
    local_now=datetime.now(timezone.utc).astimezone(ZoneInfo(timezone_name))
    day_start=local_now.replace(hour=0,minute=0,second=0,microsecond=0)
    day_end=day_start+timedelta(days=1)
    published=int(db.execute(text("""
      SELECT COUNT(*)
      FROM shrimp_animation_publish_executions
      WHERE platform='BILIBILI'
        AND account_reference IN (:account_key,:mid,:mid_ref)
        AND execution_status='PUBLISHED'
        AND publish_attempted_at>=:day_start
        AND publish_attempted_at<:day_end
    """),{
      "account_key":account["account_key"],
      "mid":account["mid"],
      "mid_ref":"MID:"+account["mid"],
      "day_start":day_start.astimezone(timezone.utc),
      "day_end":day_end.astimezone(timezone.utc),
    }).scalar_one())
    held=int(db.execute(text("""
      SELECT COUNT(*)
      FROM shrimp_bilibili_publish_reservations
      WHERE account_id=:account_id
        AND reservation_status='HELD'
        AND expires_at>now()
    """),{"account_id":account["id"]}).scalar_one())
    return published+held < limit


def create_pre_publish_reservation(
    provider_job_id,
    *,
    actor:str,
    exclude_account_key:str|None=None,
)->dict:
    clean_actor=(actor or "shrimp-bilibili-router")[:200]
    expire_reservations()
    ttl=max(1,min(
        int(settings.shrimp_bilibili_reservation_ttl_minutes),
        120,
    ))
    candidates=select_failover_sacrificial_accounts(
        limit=20,
        exclude_account_key=exclude_account_key,
    )
    if not candidates:
        raise RuntimeError("No healthy Bilibili sacrificial account is available")

    for rank,candidate in enumerate(candidates,start=1):
        try:
            with engine.begin() as db:
                existing=db.execute(text("""
                  SELECT *
                  FROM shrimp_bilibili_publish_reservations
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                    AND reservation_status='HELD'
                  FOR UPDATE
                """),{"job_id":provider_job_id}).mappings().one_or_none()
                if existing is not None:
                    return _serialize(existing)

                account=db.execute(text("""
                  SELECT * FROM shrimp_bilibili_accounts
                  WHERE account_key=:account_key
                  FOR UPDATE
                """),{"account_key":candidate["account_key"]}).mappings().one()
                if not _account_quota_available(db,dict(account)):
                    continue

                slot=db.execute(text("""
                  SELECT * FROM shrimp_bilibili_credential_slots
                  WHERE id=CAST(:slot_id AS uuid)
                  FOR UPDATE
                """),{"slot_id":candidate["credential_slot_id"]}).mappings().one()
                target=_target_for_account(
                    db,
                    account["account_key"],
                    account["mid"],
                )
                if target is None:
                    continue

                held=db.execute(text("""
                  SELECT id FROM shrimp_bilibili_publish_reservations
                  WHERE account_id=:account_id
                    AND reservation_status='HELD'
                    AND expires_at>now()
                  FOR UPDATE
                """),{"account_id":account["id"]}).mappings().one_or_none()
                if held is not None:
                    continue

                snapshot={
                    "schema_version":"shrimp-bilibili-router-v0.1",
                    "provider_job_id":str(provider_job_id),
                    "account_key":account["account_key"],
                    "mid":account["mid"],
                    "credential_slot_id":str(slot["id"]),
                    "credential_slot_key":slot["slot_key"],
                    "credential_version":int(slot["credential_version"]),
                    "target_id":str(target["id"]),
                    "target_key":target["target_key"],
                    "health_status":slot["health_status"],
                    "degradation_status":slot["degradation_status"],
                    "selection_priority":int(slot["selection_priority"]),
                    "selection_rank":rank,
                }
                selection_sha=_sha256(snapshot)
                reservation_key=secrets.token_hex(24)
                row=db.execute(text("""
                  INSERT INTO shrimp_bilibili_publish_reservations(
                    provider_job_id,account_id,credential_slot_id,target_id,
                    reservation_status,reservation_key,reserved_publish_units,
                    selection_rank,account_key,slot_key,target_key,
                    selection_snapshot,selection_sha256,expires_at,created_by)
                  VALUES(
                    CAST(:job_id AS uuid),:account_id,:slot_id,:target_id,
                    'HELD',:reservation_key,1,:selection_rank,:account_key,
                    :slot_key,:target_key,CAST(:snapshot AS jsonb),
                    :selection_sha,:expires_at,:actor)
                  RETURNING *
                """),{
                  "job_id":provider_job_id,
                  "account_id":account["id"],
                  "slot_id":slot["id"],
                  "target_id":target["id"],
                  "reservation_key":reservation_key,
                  "selection_rank":rank,
                  "account_key":account["account_key"],
                  "slot_key":slot["slot_key"],
                  "target_key":target["target_key"],
                  "snapshot":canonical_json(snapshot),
                  "selection_sha":selection_sha,
                  "expires_at":datetime.now(timezone.utc)+timedelta(minutes=ttl),
                  "actor":clean_actor,
                }).mappings().one()
                result=_serialize(row)
                result["selection_snapshot"]=snapshot
                result["replayed"]=False
                return result
        except IntegrityError:
            continue

    raise RuntimeError("All healthy Bilibili accounts are currently reserved")

def get_reservation(reservation_id)->dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_publish_reservations
          WHERE id=CAST(:id AS uuid)
        """),{"id":reservation_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Bilibili publish reservation not found")
    return _serialize(row)

def list_reservations(*,status:str|None=None,limit:int=50)->list[dict]:
    expire_reservations()
    params={"limit":max(1,min(int(limit),200))}
    sql="SELECT * FROM shrimp_bilibili_publish_reservations"
    if status:
        sql+=" WHERE reservation_status=:status"
        params["status"]=status.upper()
    sql+=" ORDER BY created_at DESC,id DESC LIMIT :limit"
    with engine.connect() as db:
        rows=db.execute(text(sql),params).mappings().all()
    return [_serialize(x) for x in rows]

def release_reservation(reservation_id,*,reason:str,actor:str)->dict:
    clean_reason=(reason or "").strip()
    if len(clean_reason)<3:
        raise ValueError("release reason is required")
    with engine.begin() as db:
        row=db.execute(text("""
          UPDATE shrimp_bilibili_publish_reservations
          SET reservation_status=CASE
                WHEN reservation_status='HELD' THEN 'RELEASED'
                ELSE reservation_status
              END,
              released_reason=CASE
                WHEN reservation_status='HELD' THEN :reason
                ELSE released_reason
              END,
              released_at=CASE
                WHEN reservation_status='HELD' THEN now()
                ELSE released_at
              END
          WHERE id=CAST(:id AS uuid)
          RETURNING *
        """),{
          "id":reservation_id,
          "reason":clean_reason[:500],
        }).mappings().one_or_none()
    if row is None:
        raise LookupError("Bilibili publish reservation not found")
    return _serialize(row)

def validate_reservation_for_plan(
    db,
    *,
    reservation_id,
    provider_job_id,
)->dict:
    db.execute(text("SELECT expire_shrimp_bilibili_reservations()"))
    row=db.execute(text("""
      SELECT r.*,a.account_status,cs.slot_status,cs.health_status,
             cs.degradation_status,cs.credential_version,
             cs.last_checked_at,pt.target_status
      FROM shrimp_bilibili_publish_reservations r
      JOIN shrimp_bilibili_accounts a ON a.id=r.account_id
      JOIN shrimp_bilibili_credential_slots cs ON cs.id=r.credential_slot_id
      JOIN shrimp_animation_publish_targets pt ON pt.id=r.target_id
      WHERE r.id=CAST(:id AS uuid)
      FOR UPDATE OF r,a,cs,pt
    """),{"id":reservation_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Bilibili publish reservation not found")
    result=dict(row)
    if str(result["provider_job_id"])!=str(provider_job_id):
        raise RuntimeError("Reservation belongs to a different provider job")
    if result["reservation_status"]!="HELD":
        raise RuntimeError("Reservation is not HELD")
    current_selection_sha=_sha256(dict(result["selection_snapshot"] or {}))
    if current_selection_sha!=result["selection_sha256"]:
        raise RuntimeError("Reservation selection snapshot drifted")
    if result["expires_at"]<=datetime.now(timezone.utc):
        raise RuntimeError("Reservation expired")
    if result["account_status"]!="ACTIVE":
        raise RuntimeError("Reserved Bilibili account is not ACTIVE")
    if result["slot_status"]!="ACTIVE":
        raise RuntimeError("Reserved credential slot is not ACTIVE")
    if result["health_status"]!="HEALTHY":
        raise RuntimeError("Reserved credential slot is not HEALTHY")
    if not credential_slot_is_fresh(result):
        raise RuntimeError("Reserved credential health evidence is stale")
    expected_version=int(
        dict(result["selection_snapshot"] or {}).get("credential_version") or 0
    )
    if expected_version!=int(result["credential_version"]):
        raise RuntimeError("Reserved credential version drifted")
    if result["degradation_status"]!="NORMAL":
        raise RuntimeError("Reserved credential slot is degraded")
    if result["target_status"]!="ACTIVE":
        raise RuntimeError("Reserved Bilibili target is not ACTIVE")
    return result

def consume_reservation(
    db,
    *,
    reservation_id,
    plan_id,
)->None:
    updated=db.execute(text("""
      UPDATE shrimp_bilibili_publish_reservations
      SET reservation_status='CONSUMED',
          consumed_by_plan_id=:plan_id,
          consumed_at=now()
      WHERE id=CAST(:id AS uuid)
        AND reservation_status='HELD'
        AND expires_at>now()
      RETURNING id
    """),{"id":reservation_id,"plan_id":plan_id}).mappings().one_or_none()
    if updated is None:
        raise RuntimeError("Reservation could not be consumed")


def rebind_pre_publish_reservation(
    reservation_id,
    *,
    actor:str,
)->dict:
    current=get_reservation(reservation_id)
    if current["reservation_status"]!="HELD":
        raise RuntimeError("Only HELD reservations can be rebound")
    if current["consumed_by_plan_id"] is not None:
        raise RuntimeError(
            "Consumed reservation cannot be rebound; create a new Step 9 Plan"
        )
    release_reservation(
        reservation_id,
        reason="FAILOVER_REBIND",
        actor=actor,
    )
    result=create_pre_publish_reservation(
        current["provider_job_id"],
        actor=actor,
        exclude_account_key=current["account_key"],
    )
    result["rebound_from_reservation_id"]=str(reservation_id)
    result["rebound_from_account_key"]=current["account_key"]
    result["requires_new_step9_plan"]=True
    return result
