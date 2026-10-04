from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_external_verification import (
    _public_key_material,
    _verify_signature,
    get_proof_bundle,
    list_proof_bundles,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    _ser,
    _sha,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _configured_key_material() -> tuple[str,str]:
    raw=settings.shrimp_bilibili_audit_signing_private_key_pem_b64.strip()
    if not raw:
        raise RuntimeError("Bilibili audit signing key is not configured")
    _,public_key_pem_b64,fingerprint=_public_key_material(raw)
    return public_key_pem_b64,fingerprint


def _get_key_by_fingerprint(fingerprint: str) -> dict | None:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_signing_keys
          WHERE key_fingerprint_sha256=:fingerprint
        """),{"fingerprint":fingerprint}).mappings().one_or_none()
    return _ser(row) if row is not None else None


def list_signing_keys(*,limit: int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_signing_keys
          ORDER BY registered_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def list_signing_key_events(*,limit: int=200) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT e.*,k.key_fingerprint_sha256,k.key_label
          FROM shrimp_bilibili_signing_key_events e
          JOIN shrimp_bilibili_signing_keys k ON k.id=e.key_id
          ORDER BY e.recorded_at DESC,e.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),1000))}).mappings().all()
    return [_ser(x) for x in rows]


def _append_event(
    *,
    key: dict,
    event_type: str,
    effective_at: datetime,
    reason: str,
    actor: str,
    previous_key_id: UUID | str | None=None,
) -> dict:
    snapshot={
        "schema_version":"shrimp-bilibili-signing-key-event-v0.1",
        "key_id":str(key["id"]),
        "key_fingerprint_sha256":key["key_fingerprint_sha256"],
        "event_type":event_type,
        "effective_at":effective_at.isoformat(),
        "previous_key_id":str(previous_key_id) if previous_key_id else None,
        "reason":reason,
        "provider_writes":False,
        "automatic_policy_change":False,
    }
    event_sha=_sha(snapshot)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_signing_key_events(
            key_id,previous_key_id,event_type,effective_at,reason,
            event_snapshot,event_sha256,recorded_by)
          VALUES(
            :key_id,:previous_key_id,:event_type,:effective_at,:reason,
            CAST(:snapshot AS jsonb),:event_sha,:actor)
          ON CONFLICT (event_sha256) DO NOTHING
          RETURNING *
        """),{
            "key_id":key["id"],
            "previous_key_id":previous_key_id,
            "event_type":event_type,
            "effective_at":effective_at,
            "reason":reason[:1000],
            "snapshot":canonical_json(snapshot),
            "event_sha":event_sha,
            "actor":actor[:200],
        }).mappings().one_or_none()
        if row is None:
            row=db.execute(text("""
              SELECT *
              FROM shrimp_bilibili_signing_key_events
              WHERE event_sha256=:sha
            """),{"sha":event_sha}).mappings().one()
    return _ser(row)


def _latest_event_for_key(key_id: UUID | str) -> dict | None:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_signing_key_events
          WHERE key_id=:key_id
          ORDER BY effective_at DESC,recorded_at DESC,id DESC
          LIMIT 1
        """),{"key_id":key_id}).mappings().one_or_none()
    return _ser(row) if row is not None else None


def key_lifecycle_state(key: dict) -> dict:
    latest=_latest_event_for_key(key["id"])
    status="UNACTIVATED"
    effective_at=None
    if latest is not None:
        status={
            "ACTIVATED":"ACTIVE",
            "ROTATED_IN":"ACTIVE",
            "RETIRED":"RETIRED",
            "REVOKED":"REVOKED",
        }[latest["event_type"]]
        effective_at=latest["effective_at"]
    return {
        **key,
        "lifecycle_status":status,
        "lifecycle_effective_at":effective_at,
    }


def list_signing_key_states(*,limit: int=100) -> list[dict]:
    return [key_lifecycle_state(x) for x in list_signing_keys(limit=limit)]


def register_configured_signing_key(
    *,
    actor: str,
    key_label: str,
    activate: bool=True,
) -> dict:
    public_key_pem_b64,fingerprint=_configured_key_material()
    existing=_get_key_by_fingerprint(fingerprint)
    if existing is None:
        with engine.begin() as db:
            row=db.execute(text("""
              INSERT INTO shrimp_bilibili_signing_keys(
                key_fingerprint_sha256,algorithm,public_key_pem_b64,
                key_label,registered_by)
              VALUES(:fingerprint,'ED25519',:public_key,:label,:actor)
              RETURNING *
            """),{
                "fingerprint":fingerprint,
                "public_key":public_key_pem_b64,
                "label":key_label[:200],
                "actor":actor[:200],
            }).mappings().one()
        existing=_ser(row)

    event=None
    if activate and key_lifecycle_state(existing)["lifecycle_status"]=="UNACTIVATED":
        event=_append_event(
            key=existing,
            event_type="ACTIVATED",
            effective_at=_now(),
            reason="Configured signing key activated",
            actor=actor,
        )
    return {
        "key":key_lifecycle_state(existing),
        "event":event,
        "private_key_persisted":False,
        "provider_write_count":0,
    }


def rotate_to_configured_signing_key(
    *,
    actor: str,
    key_label: str,
    reason: str,
) -> dict:
    new_result=register_configured_signing_key(
        actor=actor+"-register",
        key_label=key_label,
        activate=False,
    )
    new_key=new_result["key"]
    states=list_signing_key_states(limit=500)
    active=[
        x for x in states
        if x["lifecycle_status"]=="ACTIVE" and x["id"]!=new_key["id"]
    ]
    now=_now()
    retired=[]
    for old in active:
        retired.append(_append_event(
            key=old,
            event_type="RETIRED",
            effective_at=now,
            reason=reason,
            actor=actor,
        ))
    current_state=key_lifecycle_state(new_key)
    if current_state["lifecycle_status"]!="ACTIVE":
        event=_append_event(
            key=new_key,
            event_type="ROTATED_IN",
            effective_at=now,
            reason=reason,
            actor=actor,
            previous_key_id=active[0]["id"] if active else None,
        )
    else:
        event=_latest_event_for_key(new_key["id"])
    return {
        "active_key":key_lifecycle_state(new_key),
        "retired_events":retired,
        "activation_event":event,
        "historical_proofs_preserved":True,
        "provider_write_count":0,
    }


def revoke_signing_key(
    fingerprint: str,
    *,
    effective_at: datetime,
    reason: str,
    actor: str,
) -> dict:
    key=_get_key_by_fingerprint(fingerprint)
    if key is None:
        raise LookupError("Signing key not found")
    event=_append_event(
        key=key,
        event_type="REVOKED",
        effective_at=effective_at.astimezone(timezone.utc),
        reason=reason,
        actor=actor,
    )
    return {
        "key":key_lifecycle_state(key),
        "revocation_event":event,
        "historical_validity_rule":"INVALID_IF_SIGNED_AT_OR_AFTER_REVOCATION_EFFECTIVE_AT",
        "provider_write_count":0,
    }


def _events_for_fingerprint(fingerprint: str) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT e.*
          FROM shrimp_bilibili_signing_key_events e
          JOIN shrimp_bilibili_signing_keys k ON k.id=e.key_id
          WHERE k.key_fingerprint_sha256=:fingerprint
          ORDER BY e.effective_at,e.recorded_at,e.id
        """),{"fingerprint":fingerprint}).mappings().all()
    return [_ser(x) for x in rows]


def verify_bundle_with_key_registry(bundle_id: UUID) -> dict:
    bundle=get_proof_bundle(bundle_id)
    fingerprint=bundle["signing_key_fingerprint_sha256"]
    key=_get_key_by_fingerprint(fingerprint)
    issues=[]
    if key is None:
        issues.append("SIGNING_KEY_NOT_REGISTERED")
        events=[]
    else:
        if key["public_key_pem_b64"]!=bundle["public_key_pem_b64"]:
            issues.append("REGISTERED_PUBLIC_KEY_MISMATCH")
        if not _verify_signature(
            digest_sha256=bundle["bundle_sha256"],
            signature_b64=bundle["signature_b64"],
            public_key_pem_b64=key["public_key_pem_b64"],
        ):
            issues.append("REGISTERED_KEY_SIGNATURE_INVALID")
        events=_events_for_fingerprint(fingerprint)

    signed_at=bundle["generated_at"]
    activated=[
        e for e in events
        if e["event_type"] in ("ACTIVATED","ROTATED_IN")
        and e["effective_at"]<=signed_at
    ]
    if key is not None and not activated:
        issues.append("KEY_NOT_ACTIVE_AT_SIGNING_TIME")

    invalidating_revocations=[
        e for e in events
        if e["event_type"]=="REVOKED" and e["effective_at"]<=signed_at
    ]
    if invalidating_revocations:
        issues.append("KEY_REVOKED_AT_SIGNING_TIME")

    later_revocations=[
        e for e in events
        if e["event_type"]=="REVOKED" and e["effective_at"]>signed_at
    ]
    retired=[
        e for e in events
        if e["event_type"]=="RETIRED" and e["effective_at"]>=signed_at
    ]
    historical_preserved=(
        key is not None
        and not invalidating_revocations
        and not issues
    )
    return {
        "bundle_id":str(bundle_id),
        "verification_status":"PASS" if not issues else "FAIL",
        "issue_codes":sorted(set(issues)),
        "signing_key_fingerprint_sha256":fingerprint,
        "key_registry_status":(
            key_lifecycle_state(key)["lifecycle_status"] if key else "MISSING"
        ),
        "signed_at":signed_at,
        "historical_validity_preserved":historical_preserved,
        "later_revocation_count":len(later_revocations),
        "retirement_event_count":len(retired),
        "requires_private_key":False,
        "provider_writes":False,
    }


def verify_all_bundles_with_key_registry(*,limit: int=500) -> dict:
    bundles=list_proof_bundles(limit=limit)
    results=[verify_bundle_with_key_registry(UUID(x["id"])) for x in bundles]
    failed=[x for x in results if x["verification_status"]!="PASS"]
    return {
        "verification_status":"PASS" if not failed else "FAIL",
        "bundle_count":len(results),
        "failed_bundle_count":len(failed),
        "results":results,
        "multi_key_verification":True,
        "provider_writes":False,
    }


def signing_key_lifecycle_dashboard() -> dict:
    states=list_signing_key_states(limit=100)
    return {
        "signing_keys":states,
        "events":list_signing_key_events(limit=200),
        "bundle_verification":verify_all_bundles_with_key_registry(limit=500),
        "active_key_count":sum(
            1 for x in states if x["lifecycle_status"]=="ACTIVE"
        ),
        "historical_validity_preservation_enabled":True,
        "private_keys_persisted":False,
        "automatic_provider_writes":False,
        "automatic_policy_change":False,
    }
