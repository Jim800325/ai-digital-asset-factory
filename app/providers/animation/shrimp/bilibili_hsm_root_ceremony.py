from __future__ import annotations

import base64
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_hsm_custody import (
    HsmKeyMaterial,
    ensure_pkcs11_ed25519_key,
    pkcs11_readiness,
    read_pkcs11_key,
    sign_pkcs11,
    verify_pkcs11_public,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    _ser,
    _sha,
)
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    list_trust_roots,
    verify_trust_root_chain,
)


def _get_hsm_key(fingerprint: str) -> dict | None:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_hsm_keys
          WHERE fingerprint_sha256=:fp
        """),{"fp":fingerprint}).mappings().one_or_none()
    return _ser(row) if row is not None else None


def register_current_hsm_key(*,actor: str) -> dict:
    material=ensure_pkcs11_ed25519_key()
    existing=_get_hsm_key(material.fingerprint_sha256)
    if existing is not None:
        return existing
    locator={
        "provider":"PKCS11",
        "tokenLabel":material.token_label,
        "keyLabel":material.key_label,
        "keyIdHex":material.key_id_hex,
    }
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_hsm_keys(
            provider,module_path,token_label,key_label,key_id_hex,algorithm,
            public_key_pem_b64,fingerprint_sha256,provider_key_locator,
            exportable_private_key,registered_by)
          VALUES(
            'PKCS11',:module,:token,:label,:key_id,'ED25519',
            :public_key,:fp,CAST(:locator AS jsonb),false,:actor)
          RETURNING *
        """),{
            "module":None,
            "token":material.token_label,
            "label":material.key_label,
            "key_id":material.key_id_hex,
            "public_key":material.public_key_pem_b64,
            "fp":material.fingerprint_sha256,
            "locator":canonical_json(locator),
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def create_root_ceremony(
    *,
    ceremony_type: str,
    actor: str,
    participant_fingerprints: list[str] | None=None,
    threshold: int | None=None,
) -> dict:
    roots=list_trust_roots(limit=1)
    if not roots:
        raise RuntimeError("Root ceremony requires a current TUF trust root")
    root=roots[0]
    hsm=register_current_hsm_key(actor=actor+"-hsm")
    participants=participant_fingerprints or [hsm["fingerprint_sha256"]]
    required=int(threshold or 1)
    if required<1 or required>len(set(participants)):
        raise ValueError("Root ceremony threshold is invalid")
    manifest={
        "schemaVersion":"shrimp-bilibili-root-ceremony-v0.1",
        "ceremonyType":ceremony_type,
        "trustRootVersion":root["root_version"],
        "trustRootSha256":root["root_sha256"],
        "participants":sorted(set(participants)),
        "threshold":required,
        "hsmKeyFingerprintSha256":hsm["fingerprint_sha256"],
        "privateKeyExported":False,
        "providerWrites":0,
        "productionWrites":0,
        "bilibiliWrites":0,
    }
    ceremony_sha=_sha(manifest)
    signature,material=sign_pkcs11(ceremony_sha.encode("ascii"))
    verified=verify_pkcs11_public(ceremony_sha.encode("ascii"),signature,material)
    if not verified:
        raise RuntimeError("HSM root ceremony signature verification failed")
    manifest["hsmSignatureB64"]=base64.b64encode(signature).decode("ascii")
    manifest["hsmSignatureVerified"]=True
    final_sha=_sha(manifest)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_root_ceremonies(
            ceremony_type,trust_root_version,participant_fingerprints,
            threshold,ceremony_manifest,ceremony_sha256,ceremony_status,
            executed_by)
          VALUES(
            :type,:version,CAST(:participants AS jsonb),:threshold,
            CAST(:manifest AS jsonb),:sha,'PASSED',:actor)
          RETURNING *
        """),{
            "type":ceremony_type,
            "version":root["root_version"],
            "participants":canonical_json(sorted(set(participants))),
            "threshold":required,
            "manifest":canonical_json(manifest),
            "sha":final_sha,
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def generate_offline_root_backup(*,actor: str) -> dict:
    roots=list_trust_roots(limit=1)
    if not roots:
        raise RuntimeError("Offline root backup requires a current TUF root")
    root=roots[0]
    hsm=register_current_hsm_key(actor=actor+"-hsm")
    manifest={
        "schemaVersion":"shrimp-bilibili-offline-root-backup-v0.1",
        "trustRootVersion":root["root_version"],
        "trustRootSha256":root["root_sha256"],
        "trustRootSnapshot":root["root_snapshot"],
        "rootThreshold":root["root_threshold"],
        "authorizedKeyFingerprints":root["authorized_key_fingerprints"],
        "hsmKey":{
            "provider":"PKCS11",
            "fingerprintSha256":hsm["fingerprint_sha256"],
            "publicKeyPemB64":hsm["public_key_pem_b64"],
            "providerKeyLocator":hsm["provider_key_locator"],
        },
        "containsPrivateKey":False,
        "privateKeyRecovery":"HSM_OR_KMS_VENDOR_NATIVE_BACKUP_ONLY",
    }
    backup_sha=_sha(manifest)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_offline_root_backups(
            trust_root_version,trust_root_sha256,backup_manifest,
            backup_sha256,contains_private_key,generated_by)
          VALUES(
            :version,:root_sha,CAST(:manifest AS jsonb),:backup_sha,
            false,:actor)
          RETURNING *
        """),{
            "version":root["root_version"],
            "root_sha":root["root_sha256"],
            "manifest":canonical_json(manifest),
            "backup_sha":backup_sha,
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def get_offline_root_backup(backup_id: UUID) -> dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT * FROM shrimp_bilibili_offline_root_backups WHERE id=:id
        """),{"id":backup_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Offline root backup not found")
    return _ser(row)


def run_restore_drill(backup_id: UUID,*,actor: str) -> dict:
    backup=get_offline_root_backup(backup_id)
    manifest=backup["backup_manifest"]
    issues=[]
    if manifest.get("containsPrivateKey") is not False:
        issues.append("BACKUP_CONTAINS_PRIVATE_KEY")
    if _sha(manifest)!=backup["backup_sha256"]:
        issues.append("BACKUP_SHA_MISMATCH")
    roots=list_trust_roots(limit=500)
    matching=[
        x for x in roots
        if x["root_sha256"]==backup["trust_root_sha256"]
        and int(x["root_version"])==int(backup["trust_root_version"])
    ]
    if not matching:
        issues.append("RESTORED_TUF_ROOT_NOT_FOUND")
    chain=verify_trust_root_chain()
    if chain["verification_status"]!="PASS":
        issues.append("RESTORED_TUF_ROOT_CHAIN_INVALID")

    hsm_expected=manifest["hsmKey"]["fingerprintSha256"]
    try:
        material=read_pkcs11_key()
        if material.fingerprint_sha256!=hsm_expected:
            issues.append("RESTORED_HSM_KEY_FINGERPRINT_MISMATCH")
        challenge=("restore-drill:"+backup["backup_sha256"]).encode("ascii")
        signature,signing_material=sign_pkcs11(challenge)
        hsm_verified=verify_pkcs11_public(
            challenge,signature,signing_material
        )
        if not hsm_verified:
            issues.append("RESTORED_HSM_SIGNATURE_INVALID")
    except Exception:
        hsm_verified=False
        issues.append("RESTORED_HSM_KEY_UNAVAILABLE")

    threshold_verified=bool(
        matching
        and int(matching[0]["root_threshold"])>=1
        and chain["verification_status"]=="PASS"
    )
    if not threshold_verified:
        issues.append("RESTORED_ROOT_THRESHOLD_INVALID")

    issues=sorted(set(issues))
    snapshot={
        "schemaVersion":"shrimp-bilibili-root-restore-drill-v0.1",
        "backupId":str(backup_id),
        "backupSha256":backup["backup_sha256"],
        "restoredTrustRootSha256":backup["trust_root_sha256"],
        "hsmSignatureVerified":hsm_verified,
        "rootThresholdVerified":threshold_verified,
        "privateKeyExportObserved":False,
        "issueCodes":issues,
        "providerWrites":0,
        "productionWrites":0,
        "bilibiliWrites":0,
    }
    drill_sha=_sha(snapshot)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_root_restore_drills(
            backup_id,restored_trust_root_sha256,hsm_signature_verified,
            root_threshold_verified,private_key_export_observed,
            issue_codes,drill_snapshot,drill_sha256,drill_status,executed_by)
          VALUES(
            :backup,:root_sha,:hsm,:threshold,false,
            CAST(:issues AS jsonb),CAST(:snapshot AS jsonb),:sha,:status,:actor)
          RETURNING *
        """),{
            "backup":backup_id,
            "root_sha":backup["trust_root_sha256"],
            "hsm":hsm_verified,
            "threshold":threshold_verified,
            "issues":canonical_json(issues),
            "snapshot":canonical_json(snapshot),
            "sha":drill_sha,
            "status":"PASSED" if not issues else "FAILED",
            "actor":actor[:200],
        }).mappings().one()
    result=_ser(row)
    if issues:
        raise RuntimeError("Root restore drill failed: "+",".join(issues))
    return result


def hsm_root_custody_dashboard() -> dict[str,Any]:
    with engine.connect() as db:
        keys=db.execute(text("""
          SELECT id,provider,token_label,key_label,key_id_hex,algorithm,
                 fingerprint_sha256,provider_key_locator,
                 exportable_private_key,registered_at
          FROM shrimp_bilibili_hsm_keys
          ORDER BY registered_at DESC LIMIT 100
        """)).mappings().all()
        ceremonies=db.execute(text("""
          SELECT * FROM shrimp_bilibili_root_ceremonies
          ORDER BY executed_at DESC LIMIT 100
        """)).mappings().all()
        backups=db.execute(text("""
          SELECT * FROM shrimp_bilibili_offline_root_backups
          ORDER BY generated_at DESC LIMIT 100
        """)).mappings().all()
        drills=db.execute(text("""
          SELECT * FROM shrimp_bilibili_root_restore_drills
          ORDER BY executed_at DESC LIMIT 100
        """)).mappings().all()
    return {
        "hsm":pkcs11_readiness(),
        "keys":[_ser(x) for x in keys],
        "ceremonies":[_ser(x) for x in ceremonies],
        "offline_backups":[_ser(x) for x in backups],
        "restore_drills":[_ser(x) for x in drills],
        "private_key_export_allowed":False,
        "automatic_provider_writes":False,
        "references":{
            "pkcs11":"pyauth/python-pkcs11",
            "hsm_ci":"SoftHSM/SoftHSMv2",
            "root_model":"theupdateframework/python-tuf",
            "external_key_target":"openbao/openbao",
        },
    }
