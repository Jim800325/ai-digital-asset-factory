from __future__ import annotations

import base64
import copy
import os

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    bootstrap_signing_trust,
)
from app.providers.animation.shrimp.bilibili_offline_verifier import (
    verify_exported_bundle as verify_database_free_bundle,
)
from app.providers.animation.shrimp.bilibili_transparency_dsse import (
    PAYLOAD_TYPE,
    append_to_rekor_compatible,
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


def test_step10b24_rekor_compatible_merkle_checkpoint_and_offline_tamper(monkeypatch):
    _bootstrap(monkeypatch)

    first=create_dsse_attestation(
        subject_type="AUDIT_PROOF_BUNDLE",
        subject_id="rekor-compatible-1",
        subject_sha256="d"*64,
        predicate={"sequence":1},
        actor="step10b24-local-create-1",
    )
    sign_dsse_attestation_current(first["id"],actor="step10b24-local-sign-1")
    entry1=append_to_rekor_compatible(
        first["id"],actor="step10b24-local-log-1"
    )
    assert entry1["provider"]=="REKOR_COMPATIBLE"
    assert entry1["log_index"]==0
    assert entry1["tree_size"]==1

    second=create_dsse_attestation(
        subject_type="AUDIT_PROOF_BUNDLE",
        subject_id="rekor-compatible-2",
        subject_sha256="e"*64,
        predicate={"sequence":2},
        actor="step10b24-local-create-2",
    )
    sign_dsse_attestation_current(second["id"],actor="step10b24-local-sign-2")
    entry2=append_to_rekor_compatible(
        second["id"],actor="step10b24-local-log-2"
    )
    assert entry2["provider"]=="REKOR_COMPATIBLE"
    assert entry2["log_index"]==1
    assert entry2["tree_size"]==2
    assert len(entry2["inclusion_hashes"])==1

    verified=verify_offline(second["id"])
    assert verified["verification_status"]=="PASS"
    assert verified["trusted_time_source_count"]>=1
    assert verified["transparency_verified"] is True
    assert verified["requires_database"] is False
    assert verified["requires_network"] is False
    assert verified["requires_private_key"] is False

    exported=export_offline_bundle(
        second["id"],actor="step10b24-local-export"
    )
    snapshot=exported["bundle_snapshot"]
    independent=verify_database_free_bundle(
        snapshot,
        expected_trust_root_sha256=snapshot["trust_root"]["root_sha256"],
    )
    assert independent["verification_status"]=="PASS"

    tampered_proof=copy.deepcopy(snapshot)
    tampered_proof["transparency_entries"][0]["inclusion_hashes"][0]="00"*32
    broken_proof=verify_database_free_bundle(tampered_proof)
    assert broken_proof["verification_status"]=="FAIL"
    assert "REKOR_COMPATIBLE_INCLUSION_INVALID" in broken_proof["issue_codes"]

    tampered_checkpoint=copy.deepcopy(snapshot)
    receipt=tampered_checkpoint["transparency_entries"][0]["receipt_snapshot"]
    receipt["verification"]["checkpoint"]["signature"]=base64.b64encode(
        b"x"*64
    ).decode("ascii")
    broken_checkpoint=verify_database_free_bundle(tampered_checkpoint)
    assert broken_checkpoint["verification_status"]=="FAIL"
    assert "REKOR_COMPATIBLE_CHECKPOINT_INVALID" in broken_checkpoint["issue_codes"]

    tampered_set=copy.deepcopy(snapshot)
    receipt=tampered_set["transparency_entries"][0]["receipt_snapshot"]
    receipt["verification"]["signedEntryTimestamp"]["signature"]=base64.b64encode(
        b"y"*64
    ).decode("ascii")
    broken_set=verify_database_free_bundle(tampered_set)
    assert broken_set["verification_status"]=="FAIL"
    assert "REKOR_COMPATIBLE_SET_INVALID" in broken_set["issue_codes"]

    wrong_pin=verify_database_free_bundle(
        snapshot,
        expected_trust_root_sha256="00"*32,
    )
    assert wrong_pin["verification_status"]=="FAIL"
    assert "TUF_ROOT_PIN_MISMATCH" in wrong_pin["issue_codes"]

    tampered_root=copy.deepcopy(snapshot)
    tampered_root["trust_root"]["root_snapshot"]["version"]+=1
    broken_root=verify_database_free_bundle(tampered_root)
    assert broken_root["verification_status"]=="FAIL"
    assert "TUF_TRUST_ROOT_SHA_MISMATCH" in broken_root["issue_codes"]
