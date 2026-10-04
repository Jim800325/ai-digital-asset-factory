from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import text

from app.db import engine
from app.providers.animation.models import canonical_json

_ACCOUNT_KEY=re.compile(r"^[A-Za-z0-9_.-]{3,120}$")
_MID=re.compile(r"^[0-9]{1,32}$")
_ALLOWED_COPYRIGHT={"ORIGINAL","REPOST"}
_ALLOWED_COVER={"REQUIRE_ARTIFACT","OPTIONAL","NONE"}
_ALLOWED_STATUS={"ACTIVE","INACTIVE"}
_ALLOWED_MODES={"SACRIFICIAL","REAL"}

def _sha256(value:Any)->str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

def _clean_list(value:list[str] | None, *, limit:int=30, item_limit:int=80)->list[str]:
    result=[]
    for raw in value or []:
        item=str(raw).strip()
        if item and item not in result:
            if len(item)>item_limit:
                raise ValueError("tag exceeds local limit")
            result.append(item)
    if len(result)>limit:
        raise ValueError("too many tags")
    return result

def _normalize_policy(value:dict | None)->dict:
    raw=dict(value or {})
    allowed={"mode","require_global_allowlist","allow_public_visibility"}
    unknown=sorted(set(raw)-allowed)
    if unknown:
        raise ValueError("Unknown account safety policy field(s): "+", ".join(unknown))
    mode=str(raw.get("mode") or "SACRIFICIAL").upper().strip()
    if mode not in _ALLOWED_MODES:
        raise ValueError("safety_policy.mode must be SACRIFICIAL or REAL")
    return {
        "mode":mode,
        "require_global_allowlist":bool(raw.get("require_global_allowlist",True)),
        "allow_public_visibility":bool(raw.get("allow_public_visibility",False)),
    }

def _validate_timezone(name:str)->str:
    value=(name or "Asia/Shanghai").strip()
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Invalid IANA timezone") from exc
    return value

def _serialize(row:Any)->dict:
    result=dict(row)
    if result.get("id") is not None:
        result["id"]=str(result["id"])
    for key in ("publish_window_start","publish_window_end"):
        if result.get(key) is not None:
            result[key]=str(result[key])
    return result

def account_snapshot(account:dict)->dict:
    return {
        "schema_version":"shrimp-bilibili-account-v0.2",
        "account_key":account["account_key"],
        "display_name":account["display_name"],
        "mid":account["mid"],
        "account_status":account["account_status"],
        "tags":list(account.get("tags") or []),
        "default_tid":int(account["default_tid"]),
        "default_copyright":account["default_copyright"],
        "default_description":account.get("default_description") or "",
        "default_tags":list(account.get("default_tags") or []),
        "cover_strategy":account["cover_strategy"],
        "daily_publish_limit":int(account["daily_publish_limit"]),
        "publish_window_start":str(account["publish_window_start"]) if account.get("publish_window_start") else None,
        "publish_window_end":str(account["publish_window_end"]) if account.get("publish_window_end") else None,
        "timezone":account["timezone"],
        "safety_policy":dict(account.get("safety_policy") or {}),
    }

def create_bilibili_account(*,account_key:str,display_name:str,mid:str,tags:list[str]|None=None,
    default_tid:int=122,default_copyright:str="ORIGINAL",default_description:str="",
    default_tags:list[str]|None=None,cover_strategy:str="REQUIRE_ARTIFACT",
    daily_publish_limit:int=1,publish_window_start:str|None=None,publish_window_end:str|None=None,
    timezone_name:str="Asia/Shanghai",safety_policy:dict|None=None,actor:str="shrimp-account-api")->dict:
    key=(account_key or "").strip()
    if not _ACCOUNT_KEY.fullmatch(key): raise ValueError("invalid account_key")
    name=(display_name or "").strip()
    if not name: raise ValueError("display_name is required")
    clean_mid=(mid or "").strip()
    if not _MID.fullmatch(clean_mid): raise ValueError("mid must be numeric")
    copyright_value=(default_copyright or "").upper().strip()
    if copyright_value not in _ALLOWED_COPYRIGHT: raise ValueError("invalid default_copyright")
    cover=(cover_strategy or "").upper().strip()
    if cover not in _ALLOWED_COVER: raise ValueError("invalid cover_strategy")
    limit=int(daily_publish_limit)
    if limit<0 or limit>100: raise ValueError("daily_publish_limit outside guardrails")
    tz=_validate_timezone(timezone_name)
    policy=_normalize_policy(safety_policy)
    params={"account_key":key,"display_name":name,"mid":clean_mid,"tags":canonical_json(_clean_list(tags)),
      "default_tid":int(default_tid),"default_copyright":copyright_value,"default_description":str(default_description or "")[:5000],
      "default_tags":canonical_json(_clean_list(default_tags)),"cover_strategy":cover,"daily_publish_limit":limit,
      "publish_window_start":publish_window_start or None,"publish_window_end":publish_window_end or None,
      "timezone":tz,"safety_policy":canonical_json(policy),"actor":(actor or "shrimp-account-api")[:200]}
    with engine.begin() as db:
        existing=db.execute(text("SELECT * FROM shrimp_bilibili_accounts WHERE account_key=:account_key FOR UPDATE"),{"account_key":key}).mappings().one_or_none()
        if existing is not None: return _serialize(existing)
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_accounts(
            account_key,display_name,mid,tags,default_tid,default_copyright,
            default_description,default_tags,cover_strategy,daily_publish_limit,
            publish_window_start,publish_window_end,timezone,safety_policy,
            created_by,updated_by)
          VALUES(:account_key,:display_name,:mid,CAST(:tags AS jsonb),:default_tid,:default_copyright,
            :default_description,CAST(:default_tags AS jsonb),:cover_strategy,:daily_publish_limit,
            CAST(:publish_window_start AS time),CAST(:publish_window_end AS time),:timezone,
            CAST(:safety_policy AS jsonb),:actor,:actor)
          RETURNING *
        """),params).mappings().one()
    return _serialize(row)

def list_bilibili_accounts(*,include_inactive:bool=True)->list[dict]:
    sql="SELECT * FROM shrimp_bilibili_accounts"
    if not include_inactive: sql+=" WHERE account_status='ACTIVE'"
    sql+=" ORDER BY account_status,account_key"
    with engine.connect() as db:
        rows=db.execute(text(sql)).mappings().all()
    return [_serialize(x) for x in rows]

def get_bilibili_account(account_key:str)->dict:
    with engine.connect() as db:
        row=db.execute(text("SELECT * FROM shrimp_bilibili_accounts WHERE account_key=:key"),{"key":account_key}).mappings().one_or_none()
    if row is None: raise LookupError("Bilibili account not found")
    return _serialize(row)

def update_bilibili_account(account_key:str,*,changes:dict,actor:str)->dict:
    allowed={"display_name","account_status","tags","default_tid","default_copyright","default_description","default_tags",
      "cover_strategy","daily_publish_limit","publish_window_start","publish_window_end","timezone","safety_policy"}
    unknown=sorted(set(changes)-allowed)
    if unknown: raise ValueError("Unknown account field(s): "+", ".join(unknown))
    current=get_bilibili_account(account_key)
    merged={**current,**changes}
    status=str(merged["account_status"]).upper().strip()
    if status not in _ALLOWED_STATUS: raise ValueError("invalid account_status")
    copyright_value=str(merged["default_copyright"]).upper().strip()
    if copyright_value not in _ALLOWED_COPYRIGHT: raise ValueError("invalid default_copyright")
    cover=str(merged["cover_strategy"]).upper().strip()
    if cover not in _ALLOWED_COVER: raise ValueError("invalid cover_strategy")
    limit=int(merged["daily_publish_limit"])
    if limit<0 or limit>100: raise ValueError("daily_publish_limit outside guardrails")
    tz=_validate_timezone(str(merged["timezone"]))
    policy=_normalize_policy(dict(merged.get("safety_policy") or {}))
    with engine.begin() as db:
        row=db.execute(text("""
          UPDATE shrimp_bilibili_accounts SET
            display_name=:display_name,account_status=:account_status,tags=CAST(:tags AS jsonb),
            default_tid=:default_tid,default_copyright=:default_copyright,
            default_description=:default_description,default_tags=CAST(:default_tags AS jsonb),
            cover_strategy=:cover_strategy,daily_publish_limit=:daily_publish_limit,
            publish_window_start=CAST(:publish_window_start AS time),
            publish_window_end=CAST(:publish_window_end AS time),timezone=:timezone,
            safety_policy=CAST(:safety_policy AS jsonb),updated_by=:actor
          WHERE account_key=:account_key RETURNING *
        """),{
          "account_key":account_key,"display_name":str(merged["display_name"]).strip(),
          "account_status":status,"tags":canonical_json(_clean_list(list(merged.get("tags") or []))),
          "default_tid":int(merged["default_tid"]),"default_copyright":copyright_value,
          "default_description":str(merged.get("default_description") or "")[:5000],
          "default_tags":canonical_json(_clean_list(list(merged.get("default_tags") or []))),
          "cover_strategy":cover,"daily_publish_limit":limit,
          "publish_window_start":merged.get("publish_window_start") or None,
          "publish_window_end":merged.get("publish_window_end") or None,
          "timezone":tz,"safety_policy":canonical_json(policy),"actor":(actor or "shrimp-account-api")[:200],
        }).mappings().one()
    return _serialize(row)

def resolve_account_for_target(db,target:dict)->dict|None:
    if target.get("platform")!="BILIBILI" or not target.get("account_reference"): return None
    ref=str(target["account_reference"]).strip()
    mid=ref[4:] if ref.upper().startswith("MID:") else ref
    row=db.execute(text("""
      SELECT * FROM shrimp_bilibili_accounts
      WHERE account_key=:ref OR mid=:mid
      LIMIT 1
    """),{"ref":ref,"mid":mid}).mappings().one_or_none()
    return dict(row) if row else None

def apply_account_defaults_and_guard(db,account:dict,metadata:dict)->tuple[dict,dict,str]:
    if account["account_status"]!="ACTIVE": raise RuntimeError("Bilibili account is INACTIVE")
    policy=dict(account.get("safety_policy") or {})
    value=dict(metadata or {})
    if not value.get("description") and account.get("default_description"):
        value["description"]=account["default_description"]
    if not value.get("tags") and account.get("default_tags"):
        value["tags"]=list(account["default_tags"])
    if not value.get("category"):
        value["category"]=str(account["default_tid"])
        if str(value.get("visibility") or "DRAFT").upper()=="PUBLIC" and not policy.get("allow_public_visibility",False):
        raise RuntimeError("Account safety policy forbids PUBLIC visibility")
    if account["cover_strategy"]=="REQUIRE_ARTIFACT" and not value.get("cover_artifact_sha256"):
        raise RuntimeError("Account cover strategy requires cover_artifact_sha256")

    tz=ZoneInfo(account["timezone"])
    now=datetime.now(timezone.utc).astimezone(tz)
    start=account.get("publish_window_start"); end=account.get("publish_window_end")
    if start and end:
        local=now.time().replace(tzinfo=None)
        inside=(start<=local<=end) if start<=end else (local>=start or local<=end)
        if not inside: raise RuntimeError("Outside Bilibili account publish window")

    limit=int(account["daily_publish_limit"])
    if limit==0: raise RuntimeError("Bilibili account daily publishing is disabled")
    published=db.execute(text("""
      SELECT COUNT(*) FROM shrimp_animation_publish_executions
      WHERE platform='BILIBILI'
        AND account_reference IN (:mid,:mid_ref)
        AND execution_status='PUBLISHED'
        AND publish_attempted_at >= :day_start
        AND publish_attempted_at < :day_end
    """),{
      "mid":account["mid"],"mid_ref":"MID:"+account["mid"],
      "day_start":now.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc),
      "day_end":now.replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)+timedelta(days=1),
    }).scalar_one()
    if published>=limit: raise RuntimeError("Bilibili account daily publish limit reached")
    snap=account_snapshot(account)
    return value,snap,_sha256(snap)
