import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.shrimp.bilibili_external_verification import (
    _bundle_issues,
    append_export_registry,
    create_signed_proof_bundle,
    register_external_anchor,
    verify_export_registry_chain,
    verify_proof_bundle,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import _sha
from app.providers.animation.models import canonical_json


def _private_key_b64() -> str:
    key=Ed25519PrivateKey.generate()
    pem=key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode("ascii")


def _seed_audit_proof():
    audit_snapshot={
        "schema_version":"test-integrity",
        "issue_codes":[],
        "provider_writes":False,
    }
    audit_snapshot_sha=_sha(audit_snapshot)
    audit_sha=_sha({
        "audit_status":"PASS",
        "audit_snapshot_sha256":audit_snapshot_sha,
    })
    proof_snapshot={
        "schema_version":"test-audit-proof",
        "integrity_audit":{"audit_sha256":audit_sha},
        "provider_writes":False,
    }
    proof_snapshot_sha=_sha(proof_snapshot)
    proof_sha=_sha({
        "integrity_audit_id":"placeholder",
        "proof_snapshot_sha256":proof_snapshot_sha,
    })
    with engine.begin() as db:
        audit=db.execute(text("""
          INSERT INTO shrimp_bilibili_certification_integrity_audits(
            audit_status,certification_count,attestation_count,
            current_certification_count,issue_codes,audit_snapshot,
            audit_snapshot_sha256,audit_sha256,evaluated_by)
          VALUES(
            'PASS',0,0,0,'[]'::jsonb,CAST(:snapshot AS jsonb),
            :snapshot_sha,:audit_sha,'test')
          RETURNING id
        """),{
            "snapshot":canonical_json(audit_snapshot),
            "snapshot_sha":audit_snapshot_sha,
            "audit_sha":audit_sha,
        }).scalar_one()
        proof_sha=_sha({
            "integrity_audit_id":str(audit),
            "current_certification_id":None,
            "proof_snapshot_sha256":proof_snapshot_sha,
        })
        proof=db.execute(text("""
          INSERT INTO shrimp_bilibili_certification_audit_proofs(
            integrity_audit_id,current_certification_id,proof_snapshot,
            proof_snapshot_sha256,proof_sha256,generated_by)
          VALUES(
            :audit_id,NULL,CAST(:snapshot AS jsonb),:snapshot_sha,:proof_sha,'test')
          RETURNING id
        """),{
            "audit_id":audit,
            "snapshot":canonical_json(proof_snapshot),
            "snapshot_sha":proof_snapshot_sha,
            "proof_sha":proof_sha,
        }).scalar_one()
    return proof


def test_external_verification_read_api_is_safe():
    body=TestClient(app).get(
        "/v1/shrimp-animation/bilibili-external-verification"
    ).json()
    assert body["signature_algorithm"]=="ED25519"
    assert body["external_network_writes_enabled"] is False
    assert body["automatic_policy_change"] is False
    assert body["provider_writes"] is False
    assert body["secrets_redacted"] is True


def test_bundle_write_api_requires_governance_gate(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_governance_key",
        "ci-governance-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-audit-proof-bundles",
        json={"actor":"test"},
        headers={"X-Shrimp-Reliability-Governance-Key":"wrong"},
    )
    assert response.status_code==403


def test_signed_bundle_anchor_export_and_independent_verification(monkeypatch):
    _seed_audit_proof()
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_audit_signing_private_key_pem_b64",
        _private_key_b64(),
    )

    bundle=create_signed_proof_bundle(actor="test-signer")
    verified=verify_proof_bundle(bundle["id"])
    assert verified["verification_status"]=="PASS"
    assert verified["independent_verification"] is True
    assert verified["requires_private_key"] is False
    assert verified["provider_writes"] is False

    anchor=register_external_anchor(
        bundle["id"],
        anchor_provider="TEST_EXTERNAL_LOG",
        anchor_reference="receipt:test:001",
        anchor_digest_sha256=bundle["bundle_sha256"],
        receipt={"receipt_id":"test-001","accepted":True},
        actor="test-anchor",
    )
    assert anchor["anchor_digest_sha256"]==bundle["bundle_sha256"]

    exported=append_export_registry(bundle["id"],actor="test-export")
    assert exported["registry_sequence"]==1
    chain=verify_export_registry_chain()
    assert chain["verification_status"]=="PASS"
    assert chain["export_count"]==1
    assert chain["latest_export_sha256"]==exported["export_sha256"]


def test_tampered_bundle_fails_independent_verification(monkeypatch):
    _seed_audit_proof()
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_audit_signing_private_key_pem_b64",
        _private_key_b64(),
    )
    bundle=create_signed_proof_bundle(actor="test-signer")
    tampered=dict(bundle)
    tampered["bundle_snapshot"]={
        **bundle["bundle_snapshot"],
        "audit_proof_sha256":"0"*64,
    }
    issues=_bundle_issues(tampered)
    assert "BUNDLE_SHA_MISMATCH" in issues


def test_external_anchor_rejects_wrong_digest(monkeypatch):
    _seed_audit_proof()
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_audit_signing_private_key_pem_b64",
        _private_key_b64(),
    )
    bundle=create_signed_proof_bundle(actor="test-signer")
    try:
        register_external_anchor(
            bundle["id"],
            anchor_provider="TEST_EXTERNAL_LOG",
            anchor_reference="receipt:test:bad",
            anchor_digest_sha256="0"*64,
            receipt={"receipt_id":"bad"},
            actor="test-anchor",
        )
    except ValueError as exc:
        assert "must match bundle SHA-256" in str(exc)
    else:
        raise AssertionError("wrong anchor digest must be rejected")
