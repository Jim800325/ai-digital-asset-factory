from __future__ import annotations

import base64
from datetime import timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_external_verification import (
    create_signed_proof_bundle,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import _sha
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    bootstrap_signing_trust,
    revoke_signing_key,
    rotate_signing_key,
    signing_key_lifecycle_dashboard,
    verify_all_bundles_with_key_registry,
    verify_bundle_with_key_registry,
    verify_trust_root_chain,
)


def _private_key_b64() -> str:
    key=Ed25519PrivateKey.generate()
    pem=key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode("ascii")


def _seed_audit_proof() -> None:
    audit_snapshot={
        "schema_version":"step-10b22-test-integrity",
        "issue_codes":[],
        "provider_writes":False,
    }
    audit_snapshot_sha=_sha(audit_snapshot)
    audit_sha=_sha({
        "audit_status":"PASS",
        "audit_snapshot_sha256":audit_snapshot_sha,
    })
    proof_snapshot={
        "schema_version":"step-10b22-test-proof",
        "integrity_audit":{"audit_sha256":audit_sha},
        "provider_writes":False,
    }
    proof_snapshot_sha=_sha(proof_snapshot)
    with engine.begin() as db:
        audit_id=db.execute(text("""
          INSERT INTO shrimp_bilibili_certification_integrity_audits(
            audit_status,certification_count,attestation_count,
            current_certification_count,issue_codes,audit_snapshot,
            audit_snapshot_sha256,audit_sha256,evaluated_by)
          VALUES(
            'PASS',0,0,0,'[]'::jsonb,CAST(:snapshot AS jsonb),
            :snapshot_sha,:audit_sha,'step-10b22-test')
          RETURNING id
        """),{
            "snapshot":canonical_json(audit_snapshot),
            "snapshot_sha":audit_snapshot_sha,
            "audit_sha":audit_sha,
        }).scalar_one()
        proof_sha=_sha({
            "integrity_audit_id":str(audit_id),
            "current_certification_id":None,
            "proof_snapshot_sha256":proof_snapshot_sha,
        })
        db.execute(text("""
          INSERT INTO shrimp_bilibili_certification_audit_proofs(
            integrity_audit_id,current_certification_id,proof_snapshot,
            proof_snapshot_sha256,proof_sha256,generated_by)
          VALUES(
            :audit_id,NULL,CAST(:snapshot AS jsonb),
            :snapshot_sha,:proof_sha,'step-10b22-test')
        """),{
            "audit_id":audit_id,
            "snapshot":canonical_json(proof_snapshot),
            "snapshot_sha":proof_snapshot_sha,
            "proof_sha":proof_sha,
        })


def test_step10b22_bootstrap_rotation_multi_key_and_historical_validity(monkeypatch):
    key1=_private_key_b64()
    key2=_private_key_b64()
    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","LOCAL_PEM")
    monkeypatch.setattr(
        settings,"shrimp_bilibili_audit_signing_private_key_pem_b64",key1
    )

    boot=bootstrap_signing_trust(actor="test-bootstrap",key_label="key-v1")
    assert boot["trust_root"]["root_version"]==1
    assert boot["trust_root"]["transition_type"]=="BOOTSTRAP"
    assert boot["provider_write_count"]==0
    assert verify_trust_root_chain()["verification_status"]=="PASS"

    _seed_audit_proof()
    bundle1=create_signed_proof_bundle(actor="test-bundle-v1")
    v1=verify_bundle_with_key_registry(bundle1["id"])
    assert v1["verification_status"]=="PASS"
    assert v1["historical_validity_preserved"] is True
    assert v1["verification_engine"]=="securesystemslib"
    assert v1["trust_engine"]=="python-tuf"

    monkeypatch.setattr(
        settings,"shrimp_bilibili_audit_signing_private_key_pem_b64",key2
    )
    rotation=rotate_signing_key(
        actor="test-rotate",
        reason="scheduled key rotation",
        key_label="key-v2",
    )
    assert rotation["trust_root"]["root_version"]==2
    assert rotation["trust_root"]["transition_type"]=="ROTATION"
    assert rotation["provider_write_count"]==0
    assert rotation["historical_proofs_preserved"] is True

    bundle2=create_signed_proof_bundle(actor="test-bundle-v2")
    all_verified=verify_all_bundles_with_key_registry()
    assert all_verified["verification_status"]=="PASS"
    assert all_verified["bundle_count"]==2
    assert all_verified["failed_bundle_count"]==0
    assert all_verified["multi_key_verification"] is True

    old_fp=rotation["previous_key"]["key_fingerprint_sha256"]
    revoke_signing_key(
        old_fp,
        effective_at=bundle1["generated_at"]+timedelta(seconds=1),
        reason="retired key formally revoked after historical signature",
        actor="test-revoke",
    )
    historical=verify_bundle_with_key_registry(bundle1["id"])
    assert historical["verification_status"]=="PASS"
    assert historical["later_revocation_count"]==1
    assert historical["historical_validity_preserved"] is True

    dashboard=signing_key_lifecycle_dashboard()
    assert dashboard["private_keys_persisted"] is False
    assert dashboard["automatic_provider_writes"] is False
    assert dashboard["reference_projects"]["trust_root"]=="theupdateframework/python-tuf"
    assert dashboard["reference_projects"]["private_key_custody"]=="openbao/openbao"


def test_step10b22_retroactive_compromise_invalidates_historical_bundle(monkeypatch):
    key1=_private_key_b64()
    key2=_private_key_b64()
    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","LOCAL_PEM")
    monkeypatch.setattr(
        settings,"shrimp_bilibili_audit_signing_private_key_pem_b64",key1
    )
    bootstrap_signing_trust(actor="test-bootstrap",key_label="key-v1")
    _seed_audit_proof()
    bundle=create_signed_proof_bundle(actor="test-bundle")

    monkeypatch.setattr(
        settings,"shrimp_bilibili_audit_signing_private_key_pem_b64",key2
    )
    rotation=rotate_signing_key(
        actor="test-rotate",
        reason="replace key",
        key_label="key-v2",
    )
    old_fp=rotation["previous_key"]["key_fingerprint_sha256"]
    revoke_signing_key(
        old_fp,
        effective_at=bundle["generated_at"]-timedelta(seconds=1),
        reason="forensic evidence shows pre-signing compromise",
        actor="test-retroactive-revoke",
    )
    result=verify_bundle_with_key_registry(bundle["id"])
    assert result["verification_status"]=="FAIL"
    assert "KEY_REVOKED_AT_SIGNING_TIME" in result["issue_codes"]
    assert result["historical_validity_preserved"] is False


def test_step10b22_rotation_gate_is_independent(monkeypatch):
    monkeypatch.setattr(
        settings,"shrimp_bilibili_signing_key_rotation_key","rotation-secret"
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_reliability_governance_key","governance-secret"
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-signing-key-lifecycle/rotate",
        json={
            "actor":"test-api",
            "reason":"test rotation gate",
            "key_label":"next",
        },
        headers={"X-Shrimp-Signing-Key-Rotation-Key":"wrong"},
    )
    assert response.status_code==403

    monkeypatch.setattr(
        settings,"shrimp_bilibili_signing_key_rotation_key","governance-secret"
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-signing-key-lifecycle/rotate",
        json={
            "actor":"test-api",
            "reason":"test rotation gate",
            "key_label":"next",
        },
        headers={"X-Shrimp-Signing-Key-Rotation-Key":"governance-secret"},
    )
    assert response.status_code==503


def test_step10b22_cannot_revoke_last_current_tuf_key(monkeypatch):
    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","LOCAL_PEM")
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_audit_signing_private_key_pem_b64",
        _private_key_b64(),
    )
    boot=bootstrap_signing_trust(actor="test-bootstrap",key_label="only-key")
    fingerprint=boot["key"]["key_fingerprint_sha256"]
    try:
        revoke_signing_key(
            fingerprint,
            effective_at=boot["key"]["registered_at"],
            reason="must fail",
            actor="test-revoke",
        )
    except RuntimeError as exc:
        assert "last key" in str(exc)
    else:
        raise AssertionError("revoking the last current TUF key must fail closed")

    assert boot["key"]["lifecycle_status"]=="ACTIVE"
    assert verify_trust_root_chain()["verification_status"]=="PASS"
