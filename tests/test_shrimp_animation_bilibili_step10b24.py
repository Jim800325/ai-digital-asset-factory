from __future__ import annotations

import base64
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    bootstrap_signing_trust,
)
from app.providers.animation.shrimp.bilibili_transparency_dsse import (
    PAYLOAD_TYPE,
    create_dsse_attestation,
    export_offline_bundle,
    request_trusted_timestamp,
    sign_dsse_attestation_current,
    transparency_dashboard,
    verify_dsse_threshold,
    verify_exported_bundle_snapshot,
)


def _private_key_b64() -> str:
    key=Ed25519PrivateKey.generate()
    pem=key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode("ascii")


def _bootstrap(monkeypatch):
    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","LOCAL_PEM")
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_audit_signing_private_key_pem_b64",
        _private_key_b64(),
    )
    return bootstrap_signing_trust(actor="step10b24-bootstrap",key_label="tsa-root")


def test_step10b24_dsse_threshold_and_fail_closed_without_trusted_time(monkeypatch):
    _bootstrap(monkeypatch)
    att=create_dsse_attestation(
        subject_type="AUDIT_PROOF_BUNDLE",
        subject_id="proof-1",
        subject_sha256="a"*64,
        predicate={"purpose":"step10b24-acceptance"},
        actor="step10b24-create",
    )
    sign_dsse_attestation_current(att["id"],actor="step10b24-sign")
    verified=verify_dsse_threshold(att["id"])
    assert verified["verification_status"]=="PASS"
    assert verified["threshold"]==1
    assert verified["signature_count"]==1
    assert verified["payload_type"]==PAYLOAD_TYPE

    with pytest.raises(RuntimeError,match="DSSE signature threshold"):
        # unsigned attestation must never progress
        unsigned=create_dsse_attestation(
            subject_type="AUDIT_PROOF_BUNDLE",
            subject_id="unsigned",
            subject_sha256="b"*64,
            predicate={},
            actor="step10b24-create",
        )
        export_offline_bundle(unsigned["id"],actor="step10b24-export")


@pytest.mark.skipif(
    not os.getenv("SIGSTORE_TSA_CI_URL"),
    reason="Sigstore Timestamp Authority live CI service is not configured",
)
def test_step10b24_live_sigstore_tsa_and_database_free_offline_verifier(monkeypatch):
    _bootstrap(monkeypatch)
    monkeypatch.setattr(settings,"shrimp_bilibili_tsa_enabled",True)
    monkeypatch.setattr(
        settings,"shrimp_bilibili_tsa_url",os.environ["SIGSTORE_TSA_CI_URL"]
    )

    att=create_dsse_attestation(
        subject_type="ROOT_TRANSITION_APPLICATION",
        subject_id="step10b24-live-tsa",
        subject_sha256="c"*64,
        predicate={
            "source":"Step 10B.23",
            "offlineVerificationRequired":True,
        },
        actor="step10b24-create",
    )
    sign_dsse_attestation_current(att["id"],actor="step10b24-sign")
    timestamp=request_trusted_timestamp(att["id"],actor="step10b24-tsa")
    assert timestamp["timestamp_source"]=="RFC3161_TSA"
    assert timestamp["timestamp_response_b64"]
    assert "BEGIN CERTIFICATE" in timestamp["timestamp_chain_pem"]

    exported=export_offline_bundle(att["id"],actor="step10b24-export")
    assert exported["verification_status"]=="PASS"
    snapshot=exported["bundle_snapshot"]
    independent=verify_exported_bundle_snapshot(snapshot)
    assert independent["verification_status"]=="PASS"
    assert independent["offline"] is True
    assert independent["requires_database"] is False
    assert independent["requires_network"] is False
    assert independent["trusted_time_source_count"]>=1

    tampered=dict(snapshot)
    tampered["envelope"]=dict(snapshot["envelope"])
    payload=base64.b64decode(tampered["envelope"]["payload"]).decode("utf-8")
    tampered["envelope"]["payload"]=base64.b64encode(
        (payload+" ").encode("utf-8")
    ).decode("ascii")
    broken=verify_exported_bundle_snapshot(tampered)
    assert broken["verification_status"]=="FAIL"
    assert "DSSE_SIGNATURE_THRESHOLD_NOT_MET" in broken["issue_codes"]


def test_step10b24_dashboard_stays_provider_write_safe():
    dashboard=transparency_dashboard()
    assert dashboard["offline_verification"] is True
    assert dashboard["private_key_required_for_verification"] is False
    assert dashboard["automatic_provider_writes"] is False
    assert dashboard["references"]["attestation"]=="in-toto/attestation"
    assert dashboard["references"]["transparency"]=="sigstore/rekor"
