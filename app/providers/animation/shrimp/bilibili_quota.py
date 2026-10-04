from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.db import engine
from app.providers.animation.models import canonical_json

def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def _serialize(row: Any) -> dict:
    result=dict(row)
    for key in (
        "id","account_id","reservation_id","execution_claim_id",
        "execution_id","credential_slot_id","target_id",
    ):
        if result.get(key) is not None:
            result[key]=str(result[key])
    for key in ("claimed_at","published_at","settled_at","released_at","created_at"):
        if result.get(key) is not None:
            result[key]=result[key].isoformat()
    return result

def _account_context(db, account_id) -> dict:
    row=db.execute(text("""
      SELECT id,account_key,mid,timezone,daily_publish_limit
      FROM shrimp_bilibili_accounts
      WHERE id=:id
    """),{"id":account_id}).mappings().one()
    return dict(row)

def _local_quota_date(account:dict):
    tz=ZoneInfo(str(account.get("timezone") or "Asia/Shanghai"))
    return datetime.now(timezone.utc).astimezone(tz).date()

def _ledger_entry(
    db,
    *,
    account:dict,
    reservation_id,
    claim_id,
    execution_id,
    entry_type:str,
    quota_units:int,
    source_sha256:str,
    payload:dict,
    actor:str,
)->dict:
    material={
        "schema_version":"shrimp-bilibili-quota-ledger-v0.1",
        "entry_type":entry_type,
        "account_id":str(account["id"]),
        "reservation_id":str(reservation_id) if reservation_id else None,
        "execution_claim_id":str(claim_id) if claim_id else None,
        "execution_id":str(execution_id) if execution_id else None,
        "quota_units":int(quota_units),
        "local_quota_date":str(_local_quota_date(account)),
        "account_timezone":account["timezone"],
        "source_sha256":source_sha256,
        "payload":payload,
    }
    entry_sha=_sha256(material)
    params={
      "account_id":account["id"],
      "reservation_id":reservation_id,
      "claim_id":claim_id,
      "execution_id":execution_id,
      "entry_type":entry_type,
      "quota_units":int(quota_units),
      "local_date":_local_quota_date(account),
      "timezone":account["timezone"],
      "source_sha":source_sha256,
      "payload":canonical_json(payload),
      "entry_sha":entry_sha,
      "actor":(actor or "shrimp-quota-ledger")[:200],
    }
    row=db.execute(text("""
      INSERT INTO shrimp_bilibili_quota_ledger(
        account_id,reservation_id,execution_claim_id,execution_id,
        entry_type,quota_units,local_quota_date,account_timezone,
        source_sha256,entry_payload,entry_sha256,created_by)
      VALUES(
        :account_id,:reservation_id,:claim_id,:execution_id,
        :entry_type,:quota_units,:local_date,:timezone,
        :source_sha,CAST(:payload AS jsonb),:entry_sha,:actor)
      ON CONFLICT (entry_sha256) DO NOTHING
      RETURNING *
    """),params).mappings().one_or_none()
    if row is None:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_quota_ledger
          WHERE entry_sha256=:entry_sha
        """),{"entry_sha":entry_sha}).mappings().one()
    return _serialize(row)

def claim_reservation_for_execution(
    db,
    *,
    source:dict,
    execution_id,
    actor:str,
)->dict|None:
    reservation_id=source.get("reservation_id")
    if reservation_id is None:
        return None

    reservation=db.execute(text("""
      SELECT * FROM shrimp_bilibili_publish_reservations
      WHERE id=:id
      FOR UPDATE
    """),{"id":reservation_id}).mappings().one_or_none()
    if reservation is None:
        raise RuntimeError("Routed execution reservation is missing")
    if reservation["reservation_status"]!="CONSUMED":
        raise RuntimeError(
            "Execution Claim requires CONSUMED reservation"
        )
    if str(reservation["consumed_by_plan_id"])!=str(source["id"]):
        raise RuntimeError(
            "Reservation was not consumed by this Publish Plan"
        )

    existing=db.execute(text("""
      SELECT * FROM shrimp_bilibili_execution_claims
      WHERE reservation_id=:reservation_id OR execution_id=:execution_id
      FOR UPDATE
    """),{
      "reservation_id":reservation_id,
      "execution_id":execution_id,
    }).mappings().one_or_none()
    if existing is not None:
        if str(existing["execution_id"])!=str(execution_id):
            raise RuntimeError("Reservation already claimed by another execution")
        return _serialize(existing)

    active=db.execute(text("""
      SELECT id FROM shrimp_bilibili_execution_claims
      WHERE account_id=:account_id
        AND claim_status='CLAIMED'
      FOR UPDATE
    """),{"account_id":reservation["account_id"]}).mappings().one_or_none()
    if active is not None:
        raise RuntimeError("Bilibili account already has an active execution claim")

    snapshot={
        "schema_version":"shrimp-bilibili-execution-claim-v0.1",
        "reservation_id":str(reservation_id),
        "execution_id":str(execution_id),
        "publish_plan_id":str(source["id"]),
        "account_id":str(reservation["account_id"]),
        "credential_slot_id":str(reservation["credential_slot_id"]),
        "target_id":str(reservation["target_id"]),
        "reservation_sha256":reservation["selection_sha256"],
        "plan_sha256":source["plan_sha256"],
    }
    claim_sha=_sha256(snapshot)
    claim=db.execute(text("""
      INSERT INTO shrimp_bilibili_execution_claims(
        reservation_id,execution_id,account_id,credential_slot_id,target_id,
        claim_status,quota_units,claim_snapshot,claim_sha256,claimed_by)
      VALUES(
        :reservation_id,:execution_id,:account_id,:slot_id,:target_id,
        'CLAIMED',1,CAST(:snapshot AS jsonb),:claim_sha,:actor)
      RETURNING *
    """),{
      "reservation_id":reservation_id,
      "execution_id":execution_id,
      "account_id":reservation["account_id"],
      "slot_id":reservation["credential_slot_id"],
      "target_id":reservation["target_id"],
      "snapshot":canonical_json(snapshot),
      "claim_sha":claim_sha,
      "actor":(actor or "shrimp-execution-claim")[:200],
    }).mappings().one()

    db.execute(text("""
      UPDATE shrimp_bilibili_publish_reservations
      SET reservation_status='CLAIMED'
      WHERE id=:id
    """),{"id":reservation_id})
    db.execute(text("""
      UPDATE shrimp_animation_publish_executions
      SET bilibili_execution_claim_id=:claim_id
      WHERE id=:execution_id
    """),{
      "claim_id":claim["id"],
      "execution_id":execution_id,
    })

    account=_account_context(db,reservation["account_id"])
    _ledger_entry(
      db,
      account=account,
      reservation_id=reservation_id,
      claim_id=claim["id"],
      execution_id=execution_id,
      entry_type="CLAIM_CREATED",
      quota_units=0,
      source_sha256=claim_sha,
      payload=snapshot,
      actor=actor,
    )
    return _serialize(claim)

def mark_publish_committed(
    db,
    *,
    execution_id,
    source_sha256:str,
    actor:str,
)->dict|None:
    claim=db.execute(text("""
      SELECT c.*,r.id AS reservation_row_id
      FROM shrimp_bilibili_execution_claims c
      JOIN shrimp_bilibili_publish_reservations r ON r.id=c.reservation_id
      WHERE c.execution_id=:execution_id
      FOR UPDATE OF c,r
    """),{"execution_id":execution_id}).mappings().one_or_none()
    if claim is None:
        return None
    if claim["claim_status"] in {"PUBLISHED","SETTLED"}:
        return _serialize(claim)
    if claim["claim_status"]!="CLAIMED":
        raise RuntimeError("Execution Claim is not publishable")

    db.execute(text("""
      UPDATE shrimp_bilibili_execution_claims
      SET claim_status='PUBLISHED',
          published_at=COALESCE(published_at,now())
      WHERE id=:id
    """),{"id":claim["id"]})
    db.execute(text("""
      UPDATE shrimp_bilibili_publish_reservations
      SET reservation_status='PUBLISHED'
      WHERE id=:id
    """),{"id":claim["reservation_id"]})

    account=_account_context(db,claim["account_id"])
    payload={
      "schema_version":"shrimp-bilibili-quota-publish-v0.1",
      "execution_id":str(execution_id),
      "claim_id":str(claim["id"]),
      "reservation_id":str(claim["reservation_id"]),
      "provider_write_committed":True,
    }
    _ledger_entry(
      db,
      account=account,
      reservation_id=claim["reservation_id"],
      claim_id=claim["id"],
      execution_id=execution_id,
      entry_type="PUBLISH_COMMITTED",
      quota_units=1,
      source_sha256=source_sha256,
      payload=payload,
      actor=actor,
    )
    return _serialize(claim)

def settle_cleanup(
    *,
    execution_id,
    source_sha256:str,
    actor:str,
)->dict|None:
    with engine.begin() as db:
        claim=db.execute(text("""
          SELECT * FROM shrimp_bilibili_execution_claims
          WHERE execution_id=CAST(:execution_id AS uuid)
          FOR UPDATE
        """),{"execution_id":execution_id}).mappings().one_or_none()
        if claim is None:
            return None
        if claim["claim_status"]=="SETTLED":
            return _serialize(claim)
        if claim["claim_status"]!="PUBLISHED":
            raise RuntimeError("Cleanup settlement requires PUBLISHED claim")

        db.execute(text("""
          UPDATE shrimp_bilibili_execution_claims
          SET claim_status='SETTLED',
              settled_at=COALESCE(settled_at,now())
          WHERE id=:id
        """),{"id":claim["id"]})
        db.execute(text("""
          UPDATE shrimp_bilibili_publish_reservations
          SET reservation_status='SETTLED'
          WHERE id=:id
        """),{"id":claim["reservation_id"]})

        account=_account_context(db,claim["account_id"])
        _ledger_entry(
          db,
          account=account,
          reservation_id=claim["reservation_id"],
          claim_id=claim["id"],
          execution_id=execution_id,
          entry_type="CLEANUP_SETTLED",
          quota_units=0,
          source_sha256=source_sha256,
          payload={
            "schema_version":"shrimp-bilibili-quota-cleanup-v0.1",
            "execution_id":str(execution_id),
            "claim_id":str(claim["id"]),
            "cleanup_verified":True,
          },
          actor=actor,
        )
        refreshed=db.execute(text("""
          SELECT * FROM shrimp_bilibili_execution_claims
          WHERE id=:id
        """),{"id":claim["id"]}).mappings().one()
    return _serialize(refreshed)

def release_claim_after_definitive_failure(
    db,
    *,
    execution_id,
    source_sha256:str,
    reason:str,
    actor:str,
)->dict|None:
    claim=db.execute(text("""
      SELECT * FROM shrimp_bilibili_execution_claims
      WHERE execution_id=:execution_id
      FOR UPDATE
    """),{"execution_id":execution_id}).mappings().one_or_none()
    if claim is None:
        return None
    if claim["claim_status"]=="RELEASED":
        return _serialize(claim)
    if claim["claim_status"]!="CLAIMED":
        return _serialize(claim)

    db.execute(text("""
      UPDATE shrimp_bilibili_execution_claims
      SET claim_status='RELEASED',
          released_at=now(),
          release_reason=:reason
      WHERE id=:id
    """),{"id":claim["id"],"reason":reason[:500]})
    db.execute(text("""
      UPDATE shrimp_bilibili_publish_reservations
      SET reservation_status='RELEASED',
          released_at=COALESCE(released_at,now()),
          released_reason=COALESCE(released_reason,:reason)
      WHERE id=:id
    """),{"id":claim["reservation_id"],"reason":reason[:500]})

    account=_account_context(db,claim["account_id"])
    _ledger_entry(
      db,
      account=account,
      reservation_id=claim["reservation_id"],
      claim_id=claim["id"],
      execution_id=execution_id,
      entry_type="CLAIM_RELEASED",
      quota_units=0,
      source_sha256=source_sha256,
      payload={
        "schema_version":"shrimp-bilibili-quota-claim-release-v0.1",
        "execution_id":str(execution_id),
        "claim_id":str(claim["id"]),
        "reason":reason[:500],
      },
      actor=actor,
    )
    return _serialize(claim)

def quota_usage_for_account(db, account:dict) -> dict:
    local_date=_local_quota_date(account)
    committed=int(db.execute(text("""
      SELECT COALESCE(SUM(quota_units),0)
      FROM shrimp_bilibili_quota_ledger
      WHERE account_id=:account_id
        AND local_quota_date=:local_date
        AND entry_type='PUBLISH_COMMITTED'
    """),{
      "account_id":account["id"],
      "local_date":local_date,
    }).scalar_one())
    held=int(db.execute(text("""
      SELECT COUNT(*)
      FROM shrimp_bilibili_publish_reservations
      WHERE account_id=:account_id
        AND reservation_status IN ('HELD','CONSUMED')
        AND (
          reservation_status='CONSUMED'
          OR expires_at>now()
        )
    """),{"account_id":account["id"]}).scalar_one())
    claimed=int(db.execute(text("""
      SELECT COUNT(*)
      FROM shrimp_bilibili_execution_claims
      WHERE account_id=:account_id
        AND claim_status='CLAIMED'
    """),{"account_id":account["id"]}).scalar_one())
    return {
      "local_quota_date":str(local_date),
      "daily_publish_limit":int(account.get("daily_publish_limit") or 0),
      "published_units":committed,
      "held_units":held,
      "claimed_units":claimed,
      "available_units":max(
        0,
        int(account.get("daily_publish_limit") or 0)-committed-held-claimed,
      ),
    }

def list_quota_ledger(*,account_key:str|None=None,limit:int=100)->list[dict]:
    params={"limit":max(1,min(int(limit),500))}
    sql="""
      SELECT l.*,a.account_key
      FROM shrimp_bilibili_quota_ledger l
      JOIN shrimp_bilibili_accounts a ON a.id=l.account_id
    """
    if account_key:
        sql+=" WHERE a.account_key=:account_key"
        params["account_key"]=account_key
    sql+=" ORDER BY l.created_at DESC,l.id DESC LIMIT :limit"
    with engine.connect() as db:
        rows=db.execute(text(sql),params).mappings().all()
    return [_serialize(x) for x in rows]

def list_execution_claims(*,limit:int=100)->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT c.*,a.account_key,r.target_key
          FROM shrimp_bilibili_execution_claims c
          JOIN shrimp_bilibili_accounts a ON a.id=c.account_id
          JOIN shrimp_bilibili_publish_reservations r ON r.id=c.reservation_id
          ORDER BY c.claimed_at DESC,c.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_serialize(x) for x in rows]


def get_quota_usage(account_key:str)->dict:
    with engine.connect() as db:
        account=db.execute(text("""
          SELECT id,account_key,mid,timezone,daily_publish_limit
          FROM shrimp_bilibili_accounts
          WHERE account_key=:account_key
        """),{"account_key":account_key}).mappings().one_or_none()
        if account is None:
            raise LookupError("Bilibili account not found")
        usage=quota_usage_for_account(db,dict(account))
    return {
      "account_key":account_key,
      **usage,
    }
