from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from securesystemslib.signer import SSlibKey, Signature
from sqlalchemy import text
from tuf.api.metadata import Root

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_external_verification import (
    get_proof_bundle,
    list_proof_bundles,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    _ser,
    _sha,
)
from app.providers.animation.shrimp.bilibili_signing_provider import (
    SigningKeyMaterial,
    current_signing_key,
    rotate_openbao_signing_key,
    signing_provider_readiness,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_from_pem_b64(value: str) -> Ed25519PublicKey:
    raw=base64.b64decode(value.encode("ascii"),validate=True)
    key=serialization.load_pem_public_key(raw)
    if not isinstance(key,Ed25519PublicKey):
        raise ValueError("Signing registry key must be Ed25519")
    return key


def _sslib_key(material: SigningKeyMaterial | dict[str,Any]) -> SSlibKey:
    if isinstance(material,SigningKeyMaterial):
        fingerprint=material.fingerprint_sha256
        pem_b64=material.public_key_pem_b64
    else:
        fingerprint=str(material["key_fingerprint_sha256"])
        pem_b64=str(material["public_key_pem_b64"])
    return SSlibKey.from_crypto(
        _public_from_pem_b64(pem_b64),
        keyid=fingerprint,
    )


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
          SELECT e.*,k.key_fingerprint_sha256,k.key_label,
                 k.provider,k.provider_key_version
          FROM shrimp_bilibili_signing_key_events e
          JOIN shrimp_bilibili_signing_keys k ON k.id=e.key_id
          ORDER BY e.recorded_at DESC,e.id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),1000))}).mappings().all()
    return [_ser(x) for x in rows]


def list_trust_roots(*,limit: int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_signing_trust_roots
          ORDER BY root_version DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def _latest_root() -> dict | None:
    roots=list_trust_roots(limit=1)
    return roots[0] if roots else None


def _earliest_bundle_time(fingerprint: str) -> datetime | None:
    with engine.connect() as db:
        return db.execute(text("""
          SELECT MIN(generated_at)
          FROM shrimp_bilibili_audit_proof_bundles
          WHERE signing_key_fingerprint_sha256=:fingerprint
        """),{"fingerprint":fingerprint}).scalar_one()


def _register_material(
    material: SigningKeyMaterial,
    *,
    actor: str,
    key_label: str,
) -> dict:
    existing=_get_key_by_fingerprint(material.fingerprint_sha256)
    if existing is not None:
        return existing
    tuf_key=_sslib_key(material)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_signing_keys(
            key_fingerprint_sha256,algorithm,public_key_pem_b64,
            provider,provider_key_name,provider_key_version,tuf_keyid,
            key_label,registered_by)
          VALUES(
            :fingerprint,'ED25519',:public_key,
            :provider,:provider_name,:provider_version,:tuf_keyid,
            :label,:actor)
          RETURNING *
        """),{
            "fingerprint":material.fingerprint_sha256,
            "public_key":material.public_key_pem_b64,
            "provider":material.provider,
            "provider_name":material.provider_key_name,
            "provider_version":material.provider_key_version,
            "tuf_keyid":tuf_key.keyid,
            "label":key_label[:200],
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


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
        "schema_version":"shrimp-bilibili-signing-key-event-v0.2",
        "key_id":str(key["id"]),
        "key_fingerprint_sha256":key["key_fingerprint_sha256"],
        "provider":key["provider"],
        "provider_key_version":key["provider_key_version"],
        "event_type":event_type,
        "effective_at":effective_at.astimezone(timezone.utc).isoformat(),
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
              SELECT * FROM shrimp_bilibili_signing_key_events
              WHERE event_sha256=:sha
            """),{"sha":event_sha}).mappings().one()
    return _ser(row)


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


def key_lifecycle_state(key: dict) -> dict:
    events=_events_for_fingerprint(key["key_fingerprint_sha256"])
    latest=events[-1] if events else None
    status="UNACTIVATED"
    if latest:
        status={
            "ACTIVATED":"ACTIVE",
            "ROTATED_IN":"ACTIVE",
            "RETIRED":"RETIRED",
            "REVOKED":"REVOKED",
        }[latest["event_type"]]
    return {
        **key,
        "lifecycle_status":status,
        "lifecycle_effective_at":latest["effective_at"] if latest else None,
    }


def list_signing_key_states(*,limit: int=100) -> list[dict]:
    return [key_lifecycle_state(x) for x in list_signing_keys(limit=limit)]


def _build_tuf_root(
    keys: list[dict],
    *,
    version: int,
    threshold: int,
) -> dict:
    if not keys:
        raise RuntimeError("TUF root requires at least one signing key")
    if threshold<1 or threshold>len(keys):
        raise ValueError("TUF root threshold exceeds authorized key count")
    root=Root(
        version=version,
        expires=_now()+timedelta(
            days=max(1,int(settings.shrimp_bilibili_tuf_root_valid_days))
        ),
    )
    for key in keys:
        root.add_key(_sslib_key(key),Root.type)
    root.roles[Root.type].threshold=threshold
    snapshot=root.to_dict()
    snapshot["x-ai-digital-asset-factory"]={
        "purpose":"BILIBILI_AUDIT_PROOF_SIGNING",
        "historical_proof_preservation":True,
        "private_key_storage":"EXTERNAL_OR_ENVIRONMENT_ONLY",
    }
    return snapshot


def _create_trust_root(
    keys: list[dict],
    *,
    transition_type: str,
    actor: str,
    threshold: int | None=None,
) -> dict:
    previous=_latest_root()
    version=(int(previous["root_version"])+1) if previous else 1
    configured=max(1,int(
        threshold
        if threshold is not None
        else settings.shrimp_bilibili_tuf_root_threshold
    ))
    configured=min(configured,len(keys))
    snapshot=_build_tuf_root(keys,version=version,threshold=configured)
    root_sha=_sha(snapshot)
    fingerprints=sorted(x["key_fingerprint_sha256"] for x in keys)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_signing_trust_roots(
            root_version,previous_root_id,root_threshold,
            authorized_key_fingerprints,root_snapshot,root_sha256,
            transition_type,generated_by)
          VALUES(
            :version,:previous_id,:threshold,CAST(:fingerprints AS jsonb),
            CAST(:snapshot AS jsonb),:sha,:transition,:actor)
          RETURNING *
        """),{
            "version":version,
            "previous_id":previous["id"] if previous else None,
            "threshold":configured,
            "fingerprints":canonical_json(fingerprints),
            "snapshot":canonical_json(snapshot),
            "sha":root_sha,
            "transition":transition_type,
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def bootstrap_signing_trust(*,actor: str,key_label: str="primary") -> dict:
    material=current_signing_key()
    key=_register_material(material,actor=actor+"-register",key_label=key_label)
    state=key_lifecycle_state(key)
    event=None
    if state["lifecycle_status"]=="UNACTIVATED":
        earliest=_earliest_bundle_time(material.fingerprint_sha256)
        event=_append_event(
            key=key,
            event_type="ACTIVATED",
            effective_at=earliest or _now(),
            reason="Bootstrap current signing trust",
            actor=actor,
        )
    root=_latest_root()
    if root is None:
        root=_create_trust_root(
            [key],
            transition_type="BOOTSTRAP",
            actor=actor,
            threshold=1,
        )
    return {
        "key":key_lifecycle_state(key),
        "event":event,
        "trust_root":root,
        "tuf_library":"python-tuf",
        "verification_library":"securesystemslib",
        "provider_write_count":0,
        "private_key_persisted":False,
    }


def rotate_signing_key(*,actor: str,reason: str,key_label: str="rotated") -> dict:
    before=current_signing_key()
    old=_get_key_by_fingerprint(before.fingerprint_sha256)
    if old is None:
        bootstrap_signing_trust(actor=actor+"-bootstrap",key_label="pre-rotation")
        old=_get_key_by_fingerprint(before.fingerprint_sha256)
    assert old is not None

    provider_write_count=0
    if before.provider=="OPENBAO_TRANSIT":
        after=rotate_openbao_signing_key()
        provider_write_count=1
    else:
        after=current_signing_key()
        if after.fingerprint_sha256==before.fingerprint_sha256:
            raise RuntimeError(
                "LOCAL_PEM rotation requires the operator to configure a new "
                "Ed25519 private key before applying rotation"
            )

    new=_register_material(after,actor=actor+"-register",key_label=key_label)
    if new["key_fingerprint_sha256"]==old["key_fingerprint_sha256"]:
        raise RuntimeError("Signing key rotation did not produce a new key")

    now=_now()
    retired=_append_event(
        key=old,event_type="RETIRED",effective_at=now,
        reason=reason,actor=actor,
    )
    activated=_append_event(
        key=new,event_type="ROTATED_IN",effective_at=now,
        reason=reason,actor=actor,previous_key_id=old["id"],
    )
    root=_create_trust_root(
        [new],
        transition_type="ROTATION",
        actor=actor,
        threshold=1,
    )
    return {
        "previous_key":key_lifecycle_state(old),
        "active_key":key_lifecycle_state(new),
        "retired_event":retired,
        "activation_event":activated,
        "trust_root":root,
        "historical_proofs_preserved":True,
        "provider_write_count":provider_write_count,
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
    when=effective_at.astimezone(timezone.utc)
    event=_append_event(
        key=key,event_type="REVOKED",effective_at=when,
        reason=reason,actor=actor,
    )
    latest=_latest_root()
    root=None
    if latest and fingerprint in set(latest["authorized_key_fingerprints"]):
        remaining=[
            x for x in list_signing_keys(limit=500)
            if x["key_fingerprint_sha256"] in set(
                latest["authorized_key_fingerprints"]
            )
            and x["key_fingerprint_sha256"]!=fingerprint
        ]
        if not remaining:
            raise RuntimeError(
                "Cannot revoke the last key authorized by the current TUF root"
            )
        root=_create_trust_root(
            remaining,
            transition_type="REVOCATION",
            actor=actor,
            threshold=min(int(latest["root_threshold"]),len(remaining)),
        )
    return {
        "key":key_lifecycle_state(key),
        "revocation_event":event,
        "trust_root":root,
        "historical_validity_rule":
            "INVALID_IF_SIGNED_AT_OR_AFTER_REVOCATION_EFFECTIVE_AT",
        "provider_write_count":0,
    }


def verify_trust_root_chain() -> dict:
    roots=list(reversed(list_trust_roots(limit=500)))
    issues=[]
    previous=None
    for index,row in enumerate(roots,1):
        if int(row["root_version"])!=index:
            issues.append("TUF_ROOT_VERSION_GAP")
        if _sha(row["root_snapshot"])!=row["root_sha256"]:
            issues.append("TUF_ROOT_SHA_MISMATCH")
        if previous is None:
            if row["previous_root_id"] is not None:
                issues.append("TUF_ROOT_BOOTSTRAP_HAS_PREVIOUS")
        elif str(row["previous_root_id"])!=str(previous["id"]):
            issues.append("TUF_ROOT_PREVIOUS_POINTER_MISMATCH")
        try:
            root=Root.from_dict(dict(row["root_snapshot"]))
            role=root.roles[Root.type]
            expected=set(row["authorized_key_fingerprints"])
            if set(role.keyids)!=expected:
                issues.append("TUF_ROOT_KEYSET_MISMATCH")
            if int(role.threshold)!=int(row["root_threshold"]):
                issues.append("TUF_ROOT_THRESHOLD_MISMATCH")
            if role.threshold>len(role.keyids):
                issues.append("TUF_ROOT_THRESHOLD_UNSATISFIABLE")
        except (ValueError,KeyError,TypeError):
            issues.append("TUF_ROOT_METADATA_INVALID")
        previous=row
    issues=sorted(set(issues))
    return {
        "verification_status":"PASS" if not issues else "FAIL",
        "issue_codes":issues,
        "root_count":len(roots),
        "latest_root_version":int(roots[-1]["root_version"]) if roots else None,
        "python_tuf_verified":True,
        "provider_writes":False,
    }


def _root_versions_for_fingerprint(fingerprint: str) -> list[int]:
    return sorted(
        int(x["root_version"])
        for x in list_trust_roots(limit=500)
        if fingerprint in set(x["authorized_key_fingerprints"])
    )


def verify_bundle_with_key_registry(bundle_id: UUID) -> dict:
    bundle=get_proof_bundle(bundle_id)
    fingerprint=bundle["signing_key_fingerprint_sha256"]
    key=_get_key_by_fingerprint(fingerprint)
    issues=[]
    root_versions=_root_versions_for_fingerprint(fingerprint)
    if key is None:
        issues.append("SIGNING_KEY_NOT_REGISTERED")
        events=[]
    else:
        try:
            signature=Signature(
                fingerprint,
                base64.b64decode(
                    bundle["signature_b64"].encode("ascii"),
                    validate=True,
                ).hex(),
            )
            _sslib_key(key).verify_signature(
                signature,
                bundle["bundle_sha256"].encode("ascii"),
            )
        except Exception:
            issues.append("SECURESYSTEMSLIB_SIGNATURE_INVALID")
        events=_events_for_fingerprint(fingerprint)

    if not root_versions:
        issues.append("SIGNING_KEY_NOT_AUTHORIZED_BY_TUF_ROOT")

    signed_at=bundle["generated_at"]
    activations=[
        e for e in events
        if e["event_type"] in ("ACTIVATED","ROTATED_IN")
        and e["effective_at"]<=signed_at
    ]
    if key is not None and not activations:
        issues.append("KEY_NOT_ACTIVE_AT_SIGNING_TIME")

    invalidating=[
        e for e in events
        if e["event_type"]=="REVOKED"
        and e["effective_at"]<=signed_at
    ]
    if invalidating:
        issues.append("KEY_REVOKED_AT_SIGNING_TIME")

    later_revocations=[
        e for e in events
        if e["event_type"]=="REVOKED"
        and e["effective_at"]>signed_at
    ]
    issues=sorted(set(issues))
    return {
        "bundle_id":str(bundle_id),
        "verification_status":"PASS" if not issues else "FAIL",
        "issue_codes":issues,
        "signing_key_fingerprint_sha256":fingerprint,
        "tuf_root_versions":root_versions,
        "key_registry_status":(
            key_lifecycle_state(key)["lifecycle_status"] if key else "MISSING"
        ),
        "signed_at":signed_at,
        "historical_validity_preserved":not issues,
        "later_revocation_count":len(later_revocations),
        "requires_private_key":False,
        "verification_engine":"securesystemslib",
        "trust_engine":"python-tuf",
        "provider_writes":False,
    }


def verify_all_bundles_with_key_registry(*,limit: int=500) -> dict:
    bundles=list_proof_bundles(limit=limit)
    results=[verify_bundle_with_key_registry(UUID(x["id"])) for x in bundles]
    failed=[x for x in results if x["verification_status"]!="PASS"]
    chain=verify_trust_root_chain()
    return {
        "verification_status":(
            "PASS"
            if not failed and chain["verification_status"]=="PASS"
            else "FAIL"
        ),
        "bundle_count":len(results),
        "failed_bundle_count":len(failed),
        "results":results,
        "trust_root_chain":chain,
        "multi_key_verification":True,
        "historical_proof_validity_preservation":True,
        "provider_writes":False,
    }


def signing_key_lifecycle_dashboard() -> dict:
    states=list_signing_key_states(limit=100)
    roots=list_trust_roots(limit=100)
    return {
        "signing_provider":signing_provider_readiness(),
        "signing_keys":states,
        "events":list_signing_key_events(limit=200),
        "trust_roots":roots,
        "trust_root_chain":verify_trust_root_chain(),
        "bundle_verification":verify_all_bundles_with_key_registry(limit=500),
        "active_key_count":sum(
            1 for x in states if x["lifecycle_status"]=="ACTIVE"
        ),
        "historical_validity_preservation_enabled":True,
        "private_keys_persisted":False,
        "automatic_provider_writes":False,
        "automatic_policy_change":False,
        "reference_projects":{
            "trust_root":"theupdateframework/python-tuf",
            "verification":"secure-systems-lab/securesystemslib",
            "private_key_custody":"openbao/openbao",
        },
    }
