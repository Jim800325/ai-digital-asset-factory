from __future__ import annotations

import base64
import os
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.models import canonical_json
from app.providers.animation.shrimp.bilibili_external_verification import create_signed_proof_bundle
from app.providers.animation.shrimp.bilibili_multisigner_trust import (
    add_transition_signature,
    apply_root_transition,
    create_root_transition_plan,
    decide_transition,
    run_key_compromise_recovery_drill,
    transition_approval_status,
    transition_signature_status,
)
from app.providers.animation.shrimp.bilibili_openbao_live_acceptance import (
    run_openbao_live_acceptance,
)
from app.providers.animation.shrimp.bilibili_post_restore_certification import _sha
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    _register_material,
    bootstrap_signing_trust,
    revoke_signing_key,
    rotate_signing_key,
    verify_trust_root_chain,
)
from app.providers.animation.shrimp.bilibili_signing_provider import current_signing_key


def _key_b64() -> tuple[Ed25519PrivateKey,str]:
    key=Ed25519PrivateKey.generate()
    pem=key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return key,base64.b64encode(pem).decode("ascii")


def _seed_audit_proof() -> None:
    audit_snapshot={
        "schema_version":"step-10b23-test-integrity",
        "issue_codes":[],
        "provider_writes":False,
    }
    audit_snapshot_sha=_sha(audit_snapshot)
    audit_sha=_sha({"audit_status":"PASS","audit_snapshot_sha256":audit_snapshot_sha})
    proof_snapshot={
        "schema_version":"step-10b23-test-proof",
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
            :snapshot_sha,:audit_sha,'step-10b23-test')
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
            :snapshot_sha,:proof_sha,'step-10b23-test')
        """),{
            "audit_id":audit_id,
            "snapshot":canonical_json(proof_snapshot),
            "snapshot_sha":proof_snapshot_sha,
            "proof_sha":proof_sha,
        })


def _sig(key:Ed25519PrivateKey,digest:str) -> str:
    return base64.b64encode(key.sign(digest.encode("ascii"))).decode("ascii")


def test_step10b23_threshold_dual_control_and_compromise_recovery(monkeypatch):
    key1,key1_b64=_key_b64()
    key2,key2_b64=_key_b64()
    key3,key3_b64=_key_b64()

    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","LOCAL_PEM")
    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_private_key_pem_b64",key1_b64)
    boot=bootstrap_signing_trust(actor="10b23-bootstrap",key_label="root-v1")
    fp1=boot["key"]["key_fingerprint_sha256"]

    _seed_audit_proof()
    bundle1=create_signed_proof_bundle(actor="10b23-bundle-v1")

    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_private_key_pem_b64",key2_b64)
    rotation=rotate_signing_key(
        actor="10b23-rotate-v2",reason="scheduled rotation",key_label="root-v2"
    )
    fp2=rotation["active_key"]["key_fingerprint_sha256"]

    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_private_key_pem_b64",key3_b64)
    material3=current_signing_key()
    registered3=_register_material(
        material3,actor="10b23-register-v3",key_label="candidate-v3"
    )
    fp3=registered3["key_fingerprint_sha256"]

    plan=create_root_transition_plan(
        candidate_fingerprints=[fp2,fp3],
        candidate_threshold=2,
        transition_type="COMPROMISE_RECOVERY",
        actor="10b23-plan",
    )
    add_transition_signature(
        plan["id"],fingerprint=fp2,
        signature_b64=_sig(key2,plan["candidate_root_sha256"]),
        actor="signer-v2",
    )
    add_transition_signature(
        plan["id"],fingerprint=fp3,
        signature_b64=_sig(key3,plan["candidate_root_sha256"]),
        actor="signer-v3",
    )
    sig_status=transition_signature_status(plan["id"])
    assert sig_status["previous_threshold_met"] is True
    assert sig_status["candidate_threshold_met"] is True
    assert sig_status["cryptographic_transition_authorized"] is True
    assert sig_status["unique_signer_count"]==2

    decide_transition(
        plan["id"],decision="APPROVE",reason="security approver",
        approver="root-transition-approver-a",
    )
    assert transition_approval_status(plan["id"])["dual_control_authorized"] is False
    decide_transition(
        plan["id"],decision="APPROVE",reason="operations approver",
        approver="root-transition-approver-b",
    )
    approval=transition_approval_status(plan["id"])
    assert approval["dual_control_authorized"] is True
    assert approval["distinct_approvers"]==2

    applied=apply_root_transition(plan["id"],actor="10b23-apply")
    assert applied["application_status"]=="APPLIED"
    assert verify_trust_root_chain()["verification_status"]=="PASS"

    revoke_signing_key(
        fp1,
        effective_at=bundle1["generated_at"]-timedelta(seconds=1),
        reason="forensic compromise predates signature",
        actor="10b23-retroactive-revoke",
    )
    drill=run_key_compromise_recovery_drill(
        compromised_fingerprint=fp1,
        affected_bundle_ids=[bundle1["id"]],
        actor="10b23-drill",
    )
    assert drill["drill_status"]=="PASSED"


def test_step10b23_apply_fails_without_both_controls(monkeypatch):
    key1,key1_b64=_key_b64()
    key2,key2_b64=_key_b64()
    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","LOCAL_PEM")
    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_private_key_pem_b64",key1_b64)
    boot=bootstrap_signing_trust(actor="bootstrap",key_label="v1")
    fp1=boot["key"]["key_fingerprint_sha256"]

    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_private_key_pem_b64",key2_b64)
    material2=current_signing_key()
    registered2=_register_material(material2,actor="register",key_label="v2")
    fp2=registered2["key_fingerprint_sha256"]

    plan=create_root_transition_plan(
        candidate_fingerprints=[fp1,fp2],
        candidate_threshold=2,
        transition_type="ROTATION",
        actor="plan",
    )
    add_transition_signature(
        plan["id"],fingerprint=fp1,
        signature_b64=_sig(key1,plan["candidate_root_sha256"]),
        actor="s1",
    )
    add_transition_signature(
        plan["id"],fingerprint=fp2,
        signature_b64=_sig(key2,plan["candidate_root_sha256"]),
        actor="s2",
    )
    decide_transition(
        plan["id"],decision="APPROVE",reason="only one approval",
        approver="root-transition-approver-a",
    )
    with pytest.raises(RuntimeError,match="dual-control"):
        apply_root_transition(plan["id"],actor="must-fail")


def test_step10b23_api_dual_control_keys_must_be_independent(monkeypatch):
    monkeypatch.setattr(settings,"shrimp_bilibili_root_transition_approver_a_key","same")
    monkeypatch.setattr(settings,"shrimp_bilibili_root_transition_approver_b_key","same")
    response=TestClient(app).post(
        f"/v1/shrimp-animation/bilibili-root-transitions/{uuid4()}/approve-a",
        json={"decision":"APPROVE","reason":"test"},
        headers={"X-Shrimp-Root-Transition-Approver-A-Key":"same"},
    )
    assert response.status_code==503


@pytest.mark.skipif(
    not os.getenv("OPENBAO_CI_URL"),
    reason="OpenBao live CI service is not configured",
)
def test_step10b23_real_openbao_transit_live_acceptance():
    result=run_openbao_live_acceptance(
        base_url=os.environ["OPENBAO_CI_URL"],
        token=os.environ["OPENBAO_CI_TOKEN"],
        key_name="shrimp-step10b23-"+uuid4().hex[:10],
        actor="github-actions-openbao-live",
    )
    assert result["acceptance_status"]=="PASSED"
    assert result["sign_write_count"]==2
    assert result["rotate_write_count"]==1
    assert result["verification_passed"] is True
    assert result["historical_verify_passed"] is True
