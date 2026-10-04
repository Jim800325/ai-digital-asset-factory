from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_external_verification import (
    _bundle_issues,
    append_export_registry,
    create_signed_proof_bundle,
    register_external_anchor,
    verify_export_registry_chain,
    verify_proof_bundle,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import _sha


def _signing_key_b64() -> str:
    key=Ed25519PrivateKey.generate()
    pem=key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode("ascii")


def _unsafe_provider_production_write_count() -> int:
    with engine.connect() as db:
        production=db.execute(text("""
          SELECT COUNT(*)
          FROM production_release_executions
          WHERE COALESCE(prepare_write_count,0)>0
             OR production_vercel_deployment_id IS NOT NULL
             OR previous_production_deployment_id IS NOT NULL
        """)).scalar_one()
        publishing=db.execute(text("""
          SELECT COUNT(*)
          FROM shrimp_animation_publish_executions
          WHERE COALESCE(upload_write_count,0)>0
             OR COALESCE(publish_write_count,0)>0
             OR execution_adapter IN ('BILIBILI_CONTROLLED','YOUTUBE_CONTROLLED')
        """)).scalar_one()
    return int(production)+int(publishing)


def _seed_step_10b20_proof() -> str:
    audit_snapshot={
        "schema_version":"step-10b21-acceptance-integrity-v0.1",
        "certification_count":0,
        "attestation_count":0,
        "current_certification_count":0,
        "issue_codes":[],
        "checks":{
            "certification_checks":[],
            "attestation_checks":[],
            "current_certification_ids":[],
        },
        "provider_writes":False,
        "automatic_policy_change":False,
    }
    audit_snapshot_sha=_sha(audit_snapshot)
    audit_sha=_sha({
        "audit_status":"PASS",
        "audit_snapshot_sha256":audit_snapshot_sha,
    })
    with engine.begin() as db:
        audit_id=db.execute(text("""
          INSERT INTO shrimp_bilibili_certification_integrity_audits(
            audit_status,certification_count,attestation_count,
            current_certification_count,issue_codes,audit_snapshot,
            audit_snapshot_sha256,audit_sha256,evaluated_by)
          VALUES(
            'PASS',0,0,0,'[]'::jsonb,CAST(:snapshot AS jsonb),
            :snapshot_sha,:audit_sha,'step-10b21-acceptance')
          RETURNING id
        """),{
            "snapshot":canonical_json(audit_snapshot),
            "snapshot_sha":audit_snapshot_sha,
            "audit_sha":audit_sha,
        }).scalar_one()

        proof_snapshot={
            "schema_version":"shrimp-bilibili-certification-audit-proof-v0.1",
            "integrity_audit":{
                "id":str(audit_id),
                "audit_status":"PASS",
                "audit_sha256":audit_sha,
                "audit_snapshot_sha256":audit_snapshot_sha,
            },
            "current_certification":None,
            "attestation_chain":[],
            "renewal_escalations":[],
            "automatic_policy_change":False,
            "provider_writes":False,
        }
        proof_snapshot_sha=_sha(proof_snapshot)
        proof_sha=_sha({
            "integrity_audit_id":str(audit_id),
            "current_certification_id":None,
            "proof_snapshot_sha256":proof_snapshot_sha,
        })
        proof_id=db.execute(text("""
          INSERT INTO shrimp_bilibili_certification_audit_proofs(
            integrity_audit_id,current_certification_id,
            proof_snapshot,proof_snapshot_sha256,proof_sha256,generated_by)
          VALUES(
            :audit_id,NULL,CAST(:snapshot AS jsonb),
            :snapshot_sha,:proof_sha,'step-10b21-acceptance')
          RETURNING id
        """),{
            "audit_id":audit_id,
            "snapshot":canonical_json(proof_snapshot),
            "snapshot_sha":proof_snapshot_sha,
            "proof_sha":proof_sha,
        }).scalar_one()
    return str(proof_id)


def test_step_10b21_full_cryptographic_integration_acceptance(monkeypatch):
    writes_before=_unsafe_provider_production_write_count()
    assert writes_before==0

    proof_id=_seed_step_10b20_proof()
    assert proof_id

    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_audit_signing_private_key_pem_b64",
        _signing_key_b64(),
    )

    # Signed Proof Bundle
    bundle=create_signed_proof_bundle(actor="step-10b21-acceptance-signer")
    assert bundle["signature_algorithm"]=="ED25519"
    assert len(bundle["bundle_sha256"])==64
    assert bundle["bundle_snapshot"]["provider_writes"] is False

    # Independent Auditor Verification PASS, no private key required.
    verified=verify_proof_bundle(bundle["id"])
    assert verified["verification_status"]=="PASS"
    assert verified["issue_codes"]==[]
    assert verified["independent_verification"] is True
    assert verified["requires_private_key"] is False

    # External Anchor receipt must bind to the exact signed digest.
    anchor=register_external_anchor(
        bundle["id"],
        anchor_provider="CI_INDEPENDENT_TRANSPARENCY_LOG",
        anchor_reference="ci://step-10b21/anchor/001",
        anchor_digest_sha256=bundle["bundle_sha256"],
        receipt={
            "receipt_version":"v0.1",
            "accepted":True,
            "digest":bundle["bundle_sha256"],
        },
        actor="step-10b21-acceptance-anchor",
    )
    assert anchor["anchor_digest_sha256"]==bundle["bundle_sha256"]

    # Export registry append + full SHA-chain verification.
    export=append_export_registry(
        bundle["id"],
        actor="step-10b21-acceptance-export",
    )
    chain=verify_export_registry_chain()
    assert chain["verification_status"]=="PASS"
    assert chain["issue_codes"]==[]
    assert chain["export_count"]>=1
    assert chain["latest_export_sha256"]==export["export_sha256"]

    # Tamper must fail closed.
    tampered=dict(bundle)
    tampered["bundle_snapshot"]={
        **bundle["bundle_snapshot"],
        "audit_proof_sha256":"0"*64,
    }
    issues=_bundle_issues(tampered)
    assert "BUNDLE_SHA_MISMATCH" in issues

    tampered_signature=dict(bundle)
    tampered_signature["signature_b64"]=base64.b64encode(b"0"*64).decode("ascii")
    signature_issues=_bundle_issues(tampered_signature)
    assert "BUNDLE_SIGNATURE_INVALID" in signature_issues

    # Wrong external anchor digest must be rejected.
    with pytest.raises(ValueError,match="must match bundle SHA-256"):
        register_external_anchor(
            bundle["id"],
            anchor_provider="CI_INDEPENDENT_TRANSPARENCY_LOG",
            anchor_reference="ci://step-10b21/anchor/tampered",
            anchor_digest_sha256="f"*64,
            receipt={"accepted":False},
            actor="step-10b21-acceptance-anchor",
        )

    # This governance/audit path may write audit DB evidence only. It must not
    # perform any controlled provider or production deployment write.
    writes_after=_unsafe_provider_production_write_count()
    assert writes_after==writes_before==0
