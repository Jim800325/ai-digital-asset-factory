from __future__ import annotations

import base64
import os
import shutil
import json
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.config import settings
from app.providers.animation.shrimp.bilibili_hsm_custody import (
    ensure_pkcs11_ed25519_key,
    read_pkcs11_key,
    sign_pkcs11,
    verify_pkcs11_public,
)
from app.providers.animation.shrimp.bilibili_hsm_root_ceremony import (
    create_root_ceremony,
    generate_offline_root_backup,
    hsm_root_custody_dashboard,
    register_current_hsm_key,
    run_restore_drill,
)
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    bootstrap_signing_trust,
)


def _local_root_key() -> str:
    key=Ed25519PrivateKey.generate()
    pem=key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return base64.b64encode(pem).decode("ascii")


def _configure(monkeypatch):
    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","LOCAL_PEM")
    monkeypatch.setattr(
        settings,"shrimp_bilibili_audit_signing_private_key_pem_b64",
        _local_root_key(),
    )
    monkeypatch.setattr(settings,"shrimp_bilibili_hsm_provider","PKCS11")
    monkeypatch.setattr(
        settings,"shrimp_bilibili_pkcs11_module",os.environ["PKCS11_CI_MODULE"]
    )
    monkeypatch.setattr(settings,"shrimp_bilibili_pkcs11_token_label","SHRIMP-ROOT")
    monkeypatch.setattr(settings,"shrimp_bilibili_pkcs11_user_pin","1234")
    monkeypatch.setattr(settings,"shrimp_bilibili_pkcs11_key_label","shrimp-root-ed25519")
    monkeypatch.setattr(settings,"shrimp_bilibili_pkcs11_key_id_hex","10b025")


@pytest.mark.skipif(
    not os.getenv("PKCS11_CI_MODULE"),
    reason="SoftHSM2 PKCS#11 module is not configured",
)
def test_step10b25_real_softhsm_root_custody_ceremony_and_restore(monkeypatch,tmp_path):
    _configure(monkeypatch)
    bootstrap_signing_trust(actor="10b25-tuf-bootstrap",key_label="tuf-root")

    material=ensure_pkcs11_ed25519_key()
    assert material.provider=="PKCS11"
    challenge=b"step-10b25-hsm-live"
    signature,key=sign_pkcs11(challenge)
    assert verify_pkcs11_public(challenge,signature,key) is True

    registered=register_current_hsm_key(actor="10b25-register")
    assert registered["exportable_private_key"] is False
    assert registered["fingerprint_sha256"]==material.fingerprint_sha256

    ceremony=create_root_ceremony(
        ceremony_type="DISASTER_RECOVERY",
        actor="10b25-ceremony",
    )
    assert ceremony["ceremony_status"]=="PASSED"
    assert ceremony["ceremony_manifest"]["privateKeyExported"] is False
    assert ceremony["ceremony_manifest"]["hsmSignatureVerified"] is True

    backup=generate_offline_root_backup(actor="10b25-backup")
    assert backup["contains_private_key"] is False
    assert backup["backup_manifest"]["containsPrivateKey"] is False
    assert "publicKeyPemB64" in backup["backup_manifest"]["hsmKey"]
    serialized=str(backup["backup_manifest"])
    for forbidden in (
        "privateKeyPem",
        "privateKeyPemB64",
        "privateKeyBytes",
        "secretKey",
        "privateKeyMaterial",
    ):
        assert forbidden not in serialized
    assert backup["backup_manifest"]["privateKeyRecovery"]=="HSM_OR_KMS_VENDOR_NATIVE_BACKUP_ONLY"

    token_dir=Path(os.environ["SOFTHSM2_TOKEN_DIR"])
    offline_copy=tmp_path/"softhsm-offline-backup"
    shutil.copytree(token_dir,offline_copy)

    entries=list(token_dir.iterdir())
    assert entries
    disaster=tmp_path/"destroyed-token-store"
    token_dir.rename(disaster)
    token_dir.mkdir(parents=True)

    with pytest.raises(Exception):
        read_pkcs11_key()

    shutil.rmtree(token_dir)
    shutil.copytree(offline_copy,token_dir)

    env=os.environ.copy()
    env.update({
        "SHRIMP_BILIBILI_HSM_PROVIDER":"PKCS11",
        "SHRIMP_BILIBILI_PKCS11_MODULE":os.environ["PKCS11_CI_MODULE"],
        "SHRIMP_BILIBILI_PKCS11_TOKEN_LABEL":"SHRIMP-ROOT",
        "SHRIMP_BILIBILI_PKCS11_USER_PIN":"1234",
        "SHRIMP_BILIBILI_PKCS11_KEY_LABEL":"shrimp-root-ed25519",
        "SHRIMP_BILIBILI_PKCS11_KEY_ID_HEX":"10b025",
    })
    proc=subprocess.run(
        [
            sys.executable,
            "scripts/run_shrimp_hsm_restore_drill.py",
            "--backup-id",
            str(backup["id"]),
            "--actor",
            "10b25-restore-drill",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    drill=json.loads(proc.stdout.strip().splitlines()[-1])
    assert drill["drill_status"]=="PASSED"
    assert drill["hsm_signature_verified"] is True
    assert drill["root_threshold_verified"] is True
    assert drill["private_key_export_observed"] is False

    dashboard=hsm_root_custody_dashboard()
    assert dashboard["private_key_export_allowed"] is False
    assert dashboard["automatic_provider_writes"] is False
    assert dashboard["references"]["pkcs11"]=="pyauth/python-pkcs11"
    assert dashboard["references"]["hsm_ci"]=="SoftHSM/SoftHSMv2"
