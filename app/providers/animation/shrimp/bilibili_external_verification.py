from __future__ import annotations

import base64
import hashlib
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_certification_trust_audit import (
    generate_audit_proof,
    list_audit_proofs,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    _ser,
    _sha,
)
from app.providers.animation.shrimp.bilibili_signing_provider import (
    current_signing_key,
    sign_digest_sha256,
)


def _b64decode(value: str) -> bytes:
    return base64.b64decode(value.strip().encode("ascii"), validate=True)


def _public_key_material(private_key_b64: str) -> tuple[Ed25519PrivateKey, str, str]:
    raw=_b64decode(private_key_b64)
    key=serialization.load_pem_private_key(raw,password=None)
    if not isinstance(key,Ed25519PrivateKey):
        raise ValueError("Audit signing key must be an Ed25519 private key")
    public=key.public_key()
    public_pem=public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fingerprint=hashlib.sha256(
        public.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).hexdigest()
    return key,base64.b64encode(public_pem).decode("ascii"),fingerprint


def _sign_sha256(private_key_b64: str, digest_sha256: str) -> tuple[str,str,str]:
    key,public_pem_b64,fingerprint=_public_key_material(private_key_b64)
    signature=key.sign(digest_sha256.encode("ascii"))
    return (
        base64.b64encode(signature).decode("ascii"),
        public_pem_b64,
        fingerprint,
    )


def _verify_signature(
    *,
    digest_sha256: str,
    signature_b64: str,
    public_key_pem_b64: str,
) -> bool:
    try:
        public=serialization.load_pem_public_key(_b64decode(public_key_pem_b64))
        if not isinstance(public,Ed25519PublicKey):
            return False
        public.verify(
            _b64decode(signature_b64),
            digest_sha256.encode("ascii"),
        )
        return True
    except (ValueError,TypeError,InvalidSignature):
        return False


def _bundle_issues(bundle: dict[str,Any]) -> list[str]:
    issues=[]
    expected=_sha(bundle["bundle_snapshot"])
    if expected!=bundle["bundle_sha256"]:
        issues.append("BUNDLE_SHA_MISMATCH")
    if not _verify_signature(
        digest_sha256=bundle["bundle_sha256"],
        signature_b64=bundle["signature_b64"],
        public_key_pem_b64=bundle["public_key_pem_b64"],
    ):
        issues.append("BUNDLE_SIGNATURE_INVALID")
    try:
        public=serialization.load_pem_public_key(
            _b64decode(bundle["public_key_pem_b64"])
        )
        if not isinstance(public,Ed25519PublicKey):
            issues.append("PUBLIC_KEY_ALGORITHM_INVALID")
        else:
            fingerprint=hashlib.sha256(
                public.public_bytes(
                    encoding=serialization.Encoding.DER,
                    format=serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            ).hexdigest()
            if fingerprint!=bundle["signing_key_fingerprint_sha256"]:
                issues.append("SIGNING_KEY_FINGERPRINT_MISMATCH")
    except (ValueError,TypeError):
        issues.append("PUBLIC_KEY_INVALID")
    return sorted(set(issues))


def create_signed_proof_bundle(*,actor: str) -> dict:
    proofs=list_audit_proofs(limit=1)
    proof=proofs[0] if proofs else generate_audit_proof(actor=actor+"-proof")
    key=current_signing_key()
    snapshot={
        "schema_version":"shrimp-bilibili-audit-proof-bundle-v0.2",
        "audit_proof_id":str(proof["id"]),
        "audit_proof_sha256":proof["proof_sha256"],
        "audit_proof_snapshot_sha256":proof["proof_snapshot_sha256"],
        "integrity_audit_id":str(proof["integrity_audit_id"]),
        "current_certification_id":(
            str(proof["current_certification_id"])
            if proof.get("current_certification_id") else None
        ),
        "verification_profile":"ED25519-SHA256-BUNDLE-V0.2",
        "signing_provider":key.provider,
        "provider_key_name":key.provider_key_name,
        "provider_key_version":key.provider_key_version,
        "signing_key_fingerprint_sha256":key.fingerprint_sha256,
        "provider_writes":False,
        "automatic_policy_change":False,
    }
    bundle_sha=_sha(snapshot)
    signing=sign_digest_sha256(bundle_sha)
    if (
        signing.key.fingerprint_sha256!=key.fingerprint_sha256
        or signing.key.provider_key_version!=key.provider_key_version
        or signing.key.provider!=key.provider
    ):
        raise RuntimeError("Signing key changed while proof bundle was being signed")

    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_audit_proof_bundles(
            audit_proof_id,bundle_snapshot,bundle_sha256,signature_algorithm,
            signature_b64,public_key_pem_b64,signing_key_fingerprint_sha256,
            generated_by)
          VALUES(
            CAST(:proof_id AS uuid),CAST(:snapshot AS jsonb),:bundle_sha,
            'ED25519',:signature,:public_key,:fingerprint,:actor)
          ON CONFLICT (bundle_sha256,signing_key_fingerprint_sha256)
          DO NOTHING
          RETURNING *
        """),{
            "proof_id":proof["id"],
            "snapshot":canonical_json(snapshot),
            "bundle_sha":bundle_sha,
            "signature":signing.signature_b64,
            "public_key":signing.key.public_key_pem_b64,
            "fingerprint":signing.key.fingerprint_sha256,
            "actor":actor[:200],
        }).mappings().one_or_none()
        if row is None:
            row=db.execute(text("""
              SELECT *
              FROM shrimp_bilibili_audit_proof_bundles
              WHERE bundle_sha256=:bundle_sha
                AND signing_key_fingerprint_sha256=:fingerprint
              ORDER BY generated_at DESC LIMIT 1
            """),{
                "bundle_sha":bundle_sha,
                "fingerprint":signing.key.fingerprint_sha256,
            }).mappings().one()
    result=_ser(row)
    result["signing_provider"]=signing.key.provider
    result["provider_key_version"]=signing.key.provider_key_version
    result["provider_write_count"]=signing.provider_write_count
    return result


def get_proof_bundle(bundle_id: UUID) -> dict:
    with engine.connect() as db:
        row=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_audit_proof_bundles
          WHERE id=:id
        """),{"id":bundle_id}).mappings().one_or_none()
    if row is None:
        raise LookupError("Audit proof bundle not found")
    return _ser(row)


def list_proof_bundles(*,limit: int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_audit_proof_bundles
          ORDER BY generated_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def verify_proof_bundle(bundle_id: UUID) -> dict:
    bundle=get_proof_bundle(bundle_id)
    issues=_bundle_issues(bundle)
    return {
        "bundle_id":str(bundle_id),
        "verification_status":"PASS" if not issues else "FAIL",
        "issue_codes":issues,
        "bundle_sha256":bundle["bundle_sha256"],
        "signature_algorithm":bundle["signature_algorithm"],
        "signing_key_fingerprint_sha256":bundle[
            "signing_key_fingerprint_sha256"
        ],
        "independent_verification":True,
        "requires_private_key":False,
        "provider_writes":False,
    }


def register_external_anchor(
    bundle_id: UUID,
    *,
    anchor_provider: str,
    anchor_reference: str,
    anchor_digest_sha256: str,
    receipt: dict[str,Any],
    actor: str,
) -> dict:
    bundle=get_proof_bundle(bundle_id)
    if anchor_digest_sha256.lower()!=bundle["bundle_sha256"].lower():
        raise ValueError("External anchor digest must match bundle SHA-256")
    material={
        "bundle_id":str(bundle_id),
        "bundle_sha256":bundle["bundle_sha256"],
        "anchor_provider":anchor_provider.strip(),
        "anchor_reference":anchor_reference.strip(),
        "anchor_digest_sha256":anchor_digest_sha256.lower(),
        "receipt_sha256":_sha(receipt),
    }
    anchor_sha=_sha(material)
    with engine.begin() as db:
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_external_verification_anchors(
            bundle_id,anchor_provider,anchor_reference,anchor_digest_sha256,
            receipt_snapshot,receipt_sha256,anchor_sha256,registered_by)
          VALUES(
            :bundle_id,:provider,:reference,:digest,
            CAST(:receipt AS jsonb),:receipt_sha,:anchor_sha,:actor)
          ON CONFLICT (anchor_sha256) DO NOTHING
          RETURNING *
        """),{
            "bundle_id":bundle_id,
            "provider":anchor_provider.strip()[:120],
            "reference":anchor_reference.strip()[:500],
            "digest":anchor_digest_sha256.lower(),
            "receipt":canonical_json(receipt),
            "receipt_sha":material["receipt_sha256"],
            "anchor_sha":anchor_sha,
            "actor":actor[:200],
        }).mappings().one_or_none()
        if row is None:
            row=db.execute(text("""
              SELECT *
              FROM shrimp_bilibili_external_verification_anchors
              WHERE anchor_sha256=:sha
            """),{"sha":anchor_sha}).mappings().one()
    return _ser(row)


def list_external_anchors(*,limit: int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_external_verification_anchors
          ORDER BY registered_at DESC,id DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def append_export_registry(bundle_id: UUID, *,actor: str) -> dict:
    bundle=get_proof_bundle(bundle_id)
    verification=verify_proof_bundle(bundle_id)
    if verification["verification_status"]!="PASS":
        raise RuntimeError("Cannot export an invalid audit proof bundle")
    with engine.begin() as db:
        previous=db.execute(text("""
          SELECT id,registry_sequence,export_sha256
          FROM shrimp_bilibili_tamper_evident_export_registry
          ORDER BY registry_sequence DESC
          LIMIT 1
          FOR UPDATE
        """)).mappings().one_or_none()
        sequence=(int(previous["registry_sequence"])+1) if previous else 1
        snapshot={
            "schema_version":"shrimp-bilibili-tamper-evident-export-v0.1",
            "registry_sequence":sequence,
            "bundle_id":str(bundle_id),
            "bundle_sha256":bundle["bundle_sha256"],
            "signing_key_fingerprint_sha256":bundle[
                "signing_key_fingerprint_sha256"
            ],
            "previous_export_id":str(previous["id"]) if previous else None,
            "previous_export_sha256":(
                previous["export_sha256"] if previous else None
            ),
        }
        export_sha=_sha(snapshot)
        row=db.execute(text("""
          INSERT INTO shrimp_bilibili_tamper_evident_export_registry(
            registry_sequence,bundle_id,previous_export_id,
            previous_export_sha256,export_snapshot,export_sha256,exported_by)
          VALUES(
            :sequence,:bundle_id,:previous_id,:previous_sha,
            CAST(:snapshot AS jsonb),:export_sha,:actor)
          RETURNING *
        """),{
            "sequence":sequence,
            "bundle_id":bundle_id,
            "previous_id":previous["id"] if previous else None,
            "previous_sha":previous["export_sha256"] if previous else None,
            "snapshot":canonical_json(snapshot),
            "export_sha":export_sha,
            "actor":actor[:200],
        }).mappings().one()
    return _ser(row)


def list_export_registry(*,limit: int=100) -> list[dict]:
    with engine.connect() as db:
        rows=db.execute(text("""
          SELECT *
          FROM shrimp_bilibili_tamper_evident_export_registry
          ORDER BY registry_sequence DESC
          LIMIT :limit
        """),{"limit":max(1,min(int(limit),500))}).mappings().all()
    return [_ser(x) for x in rows]


def verify_export_registry_chain() -> dict:
    with engine.connect() as db:
        rows=[
            dict(x) for x in db.execute(text("""
              SELECT *
              FROM shrimp_bilibili_tamper_evident_export_registry
              ORDER BY registry_sequence,id
            """)).mappings().all()
        ]
    issues=[]
    previous=None
    for index,row in enumerate(rows):
        if int(row["registry_sequence"])!=index+1:
            issues.append("EXPORT_SEQUENCE_GAP")
        snapshot=row["export_snapshot"]
        if _sha(snapshot)!=row["export_sha256"]:
            issues.append("EXPORT_SHA_MISMATCH")
        if previous is None:
            if row["previous_export_id"] is not None:
                issues.append("FIRST_EXPORT_HAS_PREVIOUS")
        else:
            if str(row["previous_export_id"] or "")!=str(previous["id"]):
                issues.append("EXPORT_CHAIN_POINTER_MISMATCH")
            if row["previous_export_sha256"]!=previous["export_sha256"]:
                issues.append("EXPORT_CHAIN_SHA_MISMATCH")
        previous=row
    issues=sorted(set(issues))
    return {
        "verification_status":"PASS" if not issues else "FAIL",
        "issue_codes":issues,
        "export_count":len(rows),
        "latest_export_sha256":rows[-1]["export_sha256"] if rows else None,
        "provider_writes":False,
    }


def external_verification_dashboard() -> dict:
    bundles=list_proof_bundles(limit=100)
    anchors=list_external_anchors(limit=100)
    exports=list_export_registry(limit=100)
    return {
        "latest_bundle":bundles[0] if bundles else None,
        "proof_bundles":bundles,
        "external_anchors":anchors,
        "export_registry":exports,
        "export_registry_verification":verify_export_registry_chain(),
        "signature_algorithm":"ED25519",
        "external_network_writes_enabled":False,
        "automatic_policy_change":False,
        "provider_writes":False,
        "secrets_redacted":True,
    }
