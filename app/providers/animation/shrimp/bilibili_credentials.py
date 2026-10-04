from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_live_publisher import (
    BilibiliLivePublisherAdapter,
)

_SLOT_KEY=re.compile(r"^[A-Za-z0-9_.-]{3,120}$")
_ENV_PREFIX=re.compile(r"^[A-Z][A-Z0-9_]{2,120}$")

def _sha256(value:Any)->str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def _serialize(row:Any)->dict:
    result=dict(row)
    for key in ("id","account_id","slot_id"):
        if result.get(key) is not None:
            result[key]=str(result[key])
    result.pop("created_by",None)
    result.pop("updated_by",None)
    return result

def _slot_snapshot(slot:dict)->dict:
    return {
        "schema_version":"shrimp-bilibili-credential-slot-v0.1",
        "slot_key":slot["slot_key"],
        "account_id":str(slot["account_id"]),
        "env_prefix":slot["env_prefix"],
        "slot_status":slot["slot_status"],
        "credential_status":slot["credential_status"],
        "login_status":slot["login_status"],
        "mid_status":slot["mid_status"],
        "publish_permission_status":slot["publish_permission_status"],
        "health_status":slot["health_status"],
        "provider_mid":slot.get("provider_mid"),
        "last_checked_at":(
            slot["last_checked_at"].isoformat()
            if slot.get("last_checked_at") else None
        ),
    }

def slot_snapshot_sha256(slot:dict)->str:
    return _sha256(_slot_snapshot(slot))

def credential_slot_is_fresh(slot:dict)->bool:
    checked=slot.get("last_checked_at")
    if checked is None:
        return False
    if checked.tzinfo is None:
        checked=checked.replace(tzinfo=timezone.utc)
    max_age=max(1,int(settings.shrimp_bilibili_health_max_age_minutes))
    return datetime.now(timezone.utc)-checked <= timedelta(minutes=max_age)

def _env_name(prefix:str,suffix:str)->str:
    return f"{prefix}_{suffix}"

def resolve_slot_credentials(slot:dict)->dict[str,str]:
    prefix=str(slot["env_prefix"]).strip().upper()
    if not _ENV_PREFIX.fullmatch(prefix):
        raise RuntimeError("Credential slot env_prefix is invalid")
    return {
        "sessdata":os.getenv(_env_name(prefix,"SESSDATA"),"").strip(),
        "bili_jct":os.getenv(_env_name(prefix,"BILI_JCT"),"").strip(),
        "dede_user_id":os.getenv(_env_name(prefix,"DEDE_USER_ID"),"").strip(),
        "dede_user_id_ckmd5":os.getenv(
            _env_name(prefix,"DEDE_USER_ID_CKMD5"),""
        ).strip(),
    }

def credential_presence(slot:dict)->dict[str,bool]:
    creds=resolve_slot_credentials(slot)
    return {
        "sessdata_present":bool(creds["sessdata"]),
        "bili_jct_present":bool(creds["bili_jct"]),
        "dede_user_id_present":bool(creds["dede_user_id"]),
        "dede_user_id_ckmd5_present":bool(creds["dede_user_id_ckmd5"]),
        "required_bundle_present":all((
            creds["sessdata"],
            creds["bili_jct"],
            creds["dede_user_id"],
        )),
    }

def create_credential_slot(
    *,
    account_key:str,
    slot_key:str,
    env_prefix:str,
    actor:str,
)->dict:
    key=(slot_key or "").strip()
    prefix=(env_prefix or "").strip().upper()
    if not _SLOT_KEY.fullmatch(key):
        raise ValueError("invalid credential slot_key")
    if not _ENV_PREFIX.fullmatch(prefix):
        raise ValueError("invalid credential env_prefix")
    with engine.begin() as db:
        account=db.execute(text("""
          SELECT id,account_key,mid,account_status,safety_policy
          FROM shrimp_bilibili_accounts
          WHERE account_key=:account_key
          FOR UPDATE
        """),{"account_key":account_key}).mappings().one_or_none()
        if account is None:
            raise LookupError("Bilibili account not found")
        existing=db.execute(text("""
          SELECT * FROM shrimp_bilibili_credential_slots
          WHERE account_id=:account_id OR slot_key=:slot_key
          FOR UPDATE
        """),{"account_id":account["id"],"slot_key":key}).mappings().one_or_none()
        if existing is not None:
            if existing["slot_key"]!=key or existing["env_prefix"]!=prefix:
                raise RuntimeError(
                    "Account already has a different credential slot binding"
                )
            return {
                **_serialize(existing),
                "credential_presence":credential_presence(dict(existing)),
                "secrets_redacted":True,
            }
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_credential_slots(
            slot_key,account_id,env_prefix,created_by,updated_by)
          VALUES(:slot_key,:account_id,:env_prefix,:actor,:actor)
          RETURNING *
        """),{
          "slot_key":key,
          "account_id":account["id"],
          "env_prefix":prefix,
          "actor":(actor or "shrimp-credential-slot-api")[:200],
        }).mappings().one()
    return {
        **_serialize(row),
        "credential_presence":credential_presence(dict(row)),
        "secrets_redacted":True,
    }

def list_credential_slots()->list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT cs.*,a.account_key,a.display_name,a.mid,a.account_status,
                 a.safety_policy
          FROM shrimp_bilibili_credential_slots cs
          JOIN shrimp_bilibili_accounts a ON a.id=cs.account_id
          ORDER BY a.account_key
        """)).mappings().all()
    result=[]
    for row in rows:
        item=dict(row)
        result.append({
            **_serialize(item),
            "credential_presence":credential_presence(item),
            "secrets_redacted":True,
        })
    return result

def get_credential_slot(slot_key:str)->dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT cs.*,a.account_key,a.display_name,a.mid,a.account_status,
                 a.safety_policy
          FROM shrimp_bilibili_credential_slots cs
          JOIN shrimp_bilibili_accounts a ON a.id=cs.account_id
          WHERE cs.slot_key=:slot_key
        """),{"slot_key":slot_key}).mappings().one_or_none()
    if row is None:
        raise LookupError("Bilibili credential slot not found")
    item=dict(row)
    return {
        **_serialize(item),
        "credential_presence":credential_presence(item),
        "secrets_redacted":True,
    }

def set_credential_slot_status(
    slot_key:str,
    *,
    slot_status:str,
    actor:str,
)->dict:
    status=str(slot_status or "").upper().strip()
    if status not in {"ACTIVE","INACTIVE"}:
        raise ValueError("slot_status must be ACTIVE or INACTIVE")
    with engine.begin() as db:
        row=db.execute(text("""
          UPDATE shrimp_bilibili_credential_slots
          SET slot_status=:status,updated_by=:actor
          WHERE slot_key=:slot_key
          RETURNING *
        """),{
          "slot_key":slot_key,
          "status":status,
          "actor":(actor or "shrimp-credential-slot-api")[:200],
        }).mappings().one_or_none()
    if row is None:
        raise LookupError("Bilibili credential slot not found")
    return {
        **_serialize(row),
        "credential_presence":credential_presence(dict(row)),
        "secrets_redacted":True,
    }

def run_credential_health_check(
    slot_key:str,
    *,
    actor:str,
    adapter_factory=None,
)->dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT cs.*,a.account_key,a.display_name,a.mid,a.account_status,
                 a.safety_policy
          FROM shrimp_bilibili_credential_slots cs
          JOIN shrimp_bilibili_accounts a ON a.id=cs.account_id
          WHERE cs.slot_key=:slot_key
        """),{"slot_key":slot_key}).mappings().one_or_none()
    if row is None:
        raise LookupError("Bilibili credential slot not found")
    slot=dict(row)
    presence=credential_presence(slot)
    if slot["slot_status"]!="ACTIVE":
        raise RuntimeError("Credential slot is INACTIVE")
    if slot["account_status"]!="ACTIVE":
        raise RuntimeError("Bilibili account is INACTIVE")

    expected_mid=str(slot["mid"])
    evidence={
        "slot_key":slot["slot_key"],
        "account_key":slot["account_key"],
        "expected_mid":expected_mid,
        "credential_present":presence["required_bundle_present"],
        "login_ok":False,
        "mid_match":False,
        "publish_probe_ok":False,
    }
    provider_mid=None
    provider_uname=None
    provider_level=None
    failure_type=None

    if not presence["required_bundle_present"]:
        health="UNHEALTHY"
        credential_status="MISSING"
        login_status="UNKNOWN"
        mid_status="UNKNOWN"
        permission_status="UNKNOWN"
        failure_type="CredentialBundleMissing"
    else:
        credential_status="CONFIGURED"
        try:
            credentials=resolve_slot_credentials(slot)
            factory=adapter_factory or (
                lambda creds:BilibiliLivePublisherAdapter(credentials=creds)
            )
            adapter=factory(credentials)
            probe=adapter.probe_account(expected_mid=expected_mid)
            provider_mid=str(probe.get("mid") or "")
            provider_uname=str(probe.get("uname") or "")
            provider_level=int(probe.get("level") or 0)
            evidence.update({
                "login_ok":bool(probe.get("is_login")),
                "mid_match":provider_mid==expected_mid,
                "publish_probe_ok":bool(probe.get("publish_probe_ok")),
                "provider_mid":provider_mid,
                "provider_level":provider_level,
            })
            login_status="LOGGED_IN" if evidence["login_ok"] else "LOGGED_OUT"
            if not evidence["login_ok"]:
                credential_status="EXPIRED"
                failure_type="SessionExpired"
            mid_status="MATCH" if evidence["mid_match"] else "MISMATCH"
            permission_status=(
                "ALLOWED" if evidence["publish_probe_ok"] else "DENIED"
            )
            health=(
                "HEALTHY"
                if evidence["login_ok"]
                and evidence["mid_match"]
                and evidence["publish_probe_ok"]
                else "UNHEALTHY"
            )
        except Exception as exc:
            health="UNHEALTHY"
            login_status="ERROR"
            mid_status="ERROR"
            permission_status="ERROR"
            failure_type=type(exc).__name__
            evidence["failure_type"]=failure_type
            evidence["failure_message_sha256"]=_sha256(str(exc)[:1000])

    evidence_sha=_sha256(evidence)
    failure_sha=_sha256({
        "failure_type":failure_type,
        "evidence_sha256":evidence_sha,
    }) if failure_type else None

    with engine.begin() as db:
        locked=db.execute(text("""
          SELECT * FROM shrimp_bilibili_credential_slots
          WHERE id=:id FOR UPDATE
        """),{"id":slot["id"]}).mappings().one()
        db.execute(text("""
          UPDATE shrimp_bilibili_credential_slots
          SET credential_status=:credential_status,
              login_status=:login_status,
              mid_status=:mid_status,
              publish_permission_status=:permission_status,
              health_status=:health_status,
              provider_mid=:provider_mid,
              provider_uname=:provider_uname,
              provider_level=:provider_level,
              last_checked_at=now(),
              last_success_at=CASE WHEN :health_status='HEALTHY'
                THEN now() ELSE last_success_at END,
              last_failure_at=CASE WHEN :health_status<>'HEALTHY'
                THEN now() ELSE last_failure_at END,
              last_error_type=:failure_type,
              last_error_sha256=:failure_sha,
              health_evidence_sha256=:evidence_sha,
              updated_by=:actor
          WHERE id=:id
        """),{
          "id":locked["id"],
          "credential_status":credential_status,
          "login_status":login_status,
          "mid_status":mid_status,
          "permission_status":permission_status,
          "health_status":health,
          "provider_mid":provider_mid,
          "provider_uname":provider_uname,
          "provider_level":provider_level,
          "failure_type":failure_type,
          "failure_sha":failure_sha,
          "evidence_sha":evidence_sha,
          "actor":(actor or "shrimp-credential-health")[:200],
        })
        check=db.execute(text("""
          INSERT INTO shrimp_bilibili_health_checks(
            slot_id,account_id,expected_mid,provider_mid,
            login_ok,mid_match,publish_probe_ok,credential_present,
            health_status,evidence_sha256,failure_type,checked_by)
          VALUES(:slot_id,:account_id,:expected_mid,:provider_mid,
            :login_ok,:mid_match,:publish_probe_ok,:credential_present,
            :health_status,:evidence_sha,:failure_type,:actor)
          RETURNING *
        """),{
          "slot_id":locked["id"],
          "account_id":slot["account_id"],
          "expected_mid":expected_mid,
          "provider_mid":provider_mid,
          "login_ok":evidence["login_ok"],
          "mid_match":evidence["mid_match"],
          "publish_probe_ok":evidence["publish_probe_ok"],
          "credential_present":presence["required_bundle_present"],
          "health_status":health,
          "evidence_sha":evidence_sha,
          "failure_type":failure_type,
          "actor":(actor or "shrimp-credential-health")[:200],
        }).mappings().one()

    return {
        **_serialize(check),
        "slot_key":slot["slot_key"],
        "account_key":slot["account_key"],
        "credential_status":credential_status,
        "login_status":login_status,
        "mid_status":mid_status,
        "publish_permission_status":permission_status,
        "health_status":health,
        "provider_uname":provider_uname,
        "provider_level":provider_level,
        "credential_presence":presence,
        "secrets_redacted":True,
    }

def list_health_checks(slot_key:str, *, limit:int=20)->list[dict]:
    limit=max(1,min(int(limit),100))
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT hc.*
          FROM shrimp_bilibili_health_checks hc
          JOIN shrimp_bilibili_credential_slots cs ON cs.id=hc.slot_id
          WHERE cs.slot_key=:slot_key
          ORDER BY hc.checked_at DESC,hc.id DESC
          LIMIT :limit
        """),{"slot_key":slot_key,"limit":limit}).mappings().all()
    return [_serialize(row) for row in rows]

def select_healthy_sacrificial_account()->dict|None:
    allowed=set(settings.shrimp_publish_execution_allowed_account_ref_list)
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT a.*,cs.id AS credential_slot_id,cs.slot_key,
                 cs.env_prefix,cs.health_status,cs.last_checked_at,
                 cs.health_evidence_sha256
          FROM shrimp_bilibili_accounts a
          JOIN shrimp_bilibili_credential_slots cs ON cs.account_id=a.id
          WHERE a.account_status='ACTIVE'
            AND cs.slot_status='ACTIVE'
            AND cs.health_status='HEALTHY'
            AND cs.last_checked_at >= (
              now() - (:max_age * interval '1 minute')
            )
          ORDER BY cs.last_checked_at DESC NULLS LAST,a.account_key
        """),{
          "max_age":max(
            1,
            int(settings.shrimp_bilibili_health_max_age_minutes),
          )
        }).mappings().all()
    for row in rows:
        item=dict(row)
        policy=dict(item.get("safety_policy") or {})
        if str(policy.get("mode") or "").upper()!="SACRIFICIAL":
            continue
        refs={item["account_key"],item["mid"],"MID:"+item["mid"]}
        if policy.get("require_global_allowlist",True) and not (refs & allowed):
            continue
        return {
            "account_key":item["account_key"],
            "display_name":item["display_name"],
            "mid":item["mid"],
            "credential_slot_id":str(item["credential_slot_id"]),
            "credential_slot_key":item["slot_key"],
            "health_status":item["health_status"],
            "last_checked_at":(
                item["last_checked_at"].isoformat()
                if item.get("last_checked_at") else None
            ),
            "selection_reason":"LATEST_HEALTHY_SACRIFICIAL_ACCOUNT",
            "secrets_redacted":True,
        }
    return None

def get_slot_for_account(db,account_id)->dict|None:
    row=db.execute(text("""
      SELECT * FROM shrimp_bilibili_credential_slots
      WHERE account_id=:account_id
      LIMIT 1
    """),{"account_id":account_id}).mappings().one_or_none()
    return dict(row) if row else None

def credential_slot_snapshot(slot:dict)->dict:
    return _slot_snapshot(slot)

def resolve_credential_slot_for_account(db,account_id)->dict|None:
    return get_slot_for_account(db,account_id)

def build_adapter_for_execution(execution:dict):
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT cs.*
          FROM shrimp_animation_publish_plans pp
          JOIN shrimp_bilibili_credential_slots cs
            ON cs.id=pp.credential_slot_id
          WHERE pp.id=CAST(:plan_id AS uuid)
        """),{"plan_id":execution["publish_plan_id"]}).mappings().one_or_none()
    if row is None:
        return BilibiliLivePublisherAdapter()
    slot=dict(row)
    if slot["slot_status"]!="ACTIVE":
        raise RuntimeError("Bound Bilibili credential slot is INACTIVE")
    if slot["health_status"]!="HEALTHY":
        raise RuntimeError("Bound Bilibili credential slot is not HEALTHY")
    if not credential_slot_is_fresh(slot):
        raise RuntimeError("Bound Bilibili credential health check is stale")
    if slot["mid_status"]!="MATCH":
        raise RuntimeError("Bound Bilibili credential slot MID is not verified")
    if slot["publish_permission_status"]!="ALLOWED":
        raise RuntimeError(
            "Bound Bilibili credential slot lacks publish permission"
        )
    return BilibiliLivePublisherAdapter(
        credentials=resolve_slot_credentials(slot)
    )
