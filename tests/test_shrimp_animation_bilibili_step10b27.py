from __future__ import annotations

import base64
import hashlib
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from app.config import settings
from app.providers.animation.shrimp.bilibili_external_kms import (
    ExternalKmsKeyInfo,
    ExternalKmsSignature,
)
from app.providers.animation.shrimp.bilibili_live_cloud_kms import (
    LiveCloudResource,
    require_sacrificial_name,
)
from app.providers.animation.shrimp.bilibili_live_cloud_kms_acceptance import (
    live_cloud_kms_dashboard,
    run_live_cross_cloud_acceptance,
    run_live_provider_acceptance,
)
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    bootstrap_signing_trust,
)


class LocalAdapter:
    def __init__(self,provider_type:str,provider_ref:str):
        self.provider_type=provider_type
        self.provider_ref=provider_ref
        self.private=ec.generate_private_key(ec.SECP256R1())
        self.enabled=True

    def key_info(self):
        pem=self.private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        der=self.private.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return ExternalKmsKeyInfo(
            provider_type=self.provider_type,
            provider_ref=self.provider_ref,
            signing_algorithm="ECDSA_P256_SHA256",
            public_key_pem_b64=base64.b64encode(pem).decode("ascii"),
            public_key_fingerprint_sha256=hashlib.sha256(der).hexdigest(),
            key_version="1",
        )

    def sign_digest(self,digest:bytes):
        if not self.enabled:
            raise RuntimeError("key disabled")
        sig=self.private.sign(
            digest,ec.ECDSA(Prehashed(hashes.SHA256()))
        )
        return ExternalKmsSignature(
            provider_ref=self.provider_ref,
            signature_b64=base64.b64encode(sig).decode("ascii"),
            key_version="1",
        )

    def verify_digest(self,digest:bytes,signature:ExternalKmsSignature):
        try:
            self.private.public_key().verify(
                base64.b64decode(signature.signature_b64),
                digest,
                ec.ECDSA(Prehashed(hashes.SHA256())),
            )
            return True
        except Exception:
            return False

    def health(self):
        return {"healthy":self.enabled,"provider_ref":self.provider_ref}


class FakeLifecycle:
    def __init__(self,provider_type:str):
        self.provider_type=provider_type
        self.resource=None
        self.disable_calls=0

    def create_sacrificial(self,name:str):
        require_sacrificial_name(name)
        adapter=LocalAdapter(
            self.provider_type,
            f"{self.provider_type.lower()}-live:{name}",
        )
        self.resource=LiveCloudResource(
            provider_type=self.provider_type,
            provider_ref=adapter.provider_ref,
            resource_name=name,
            resource_locator={
                "sacrificialName":name,
                "providerType":self.provider_type,
            },
            adapter=adapter,
        )
        return self.resource

    def disable(self,resource):
        self.disable_calls+=1
        resource.adapter.enabled=False
        return {"disabled":True}

    def cleanup_readback(self,resource):
        return {
            "enabled":resource.adapter.enabled,
            "cleanupVerified":not resource.adapter.enabled,
        }


def _bootstrap(monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key=Ed25519PrivateKey.generate()
    pem=key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    monkeypatch.setattr(settings,"shrimp_bilibili_audit_signing_provider","LOCAL_PEM")
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_audit_signing_private_key_pem_b64",
        base64.b64encode(pem).decode("ascii"),
    )
    monkeypatch.setattr(settings,"shrimp_bilibili_live_cloud_kms_acceptance_enabled",True)
    monkeypatch.setattr(settings,"shrimp_bilibili_live_cloud_kms_cleanup_enabled",True)
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_allowed_name_prefix",
        "shrimp-sacrificial-",
    )
    bootstrap_signing_trust(actor="10b27-bootstrap",key_label="tuf-root")


def test_step10b27_single_provider_acceptance_requires_cleanup(monkeypatch):
    _bootstrap(monkeypatch)
    lifecycle=FakeLifecycle("AWS_KMS")
    result=run_live_provider_acceptance(
        "AWS_KMS",actor="10b27-single",lifecycle=lifecycle
    )
    assert result["acceptance_status"]=="CLEANUP_VERIFIED"
    assert result["live_signature_verified"] is True
    assert result["cleanup_verified"] is True
    assert result["post_cleanup_sign_blocked"] is True
    assert result["acceptance_snapshot"]["credentialsPersisted"] is False
    assert result["acceptance_snapshot"]["productionWrites"]==0
    assert result["acceptance_snapshot"]["bilibiliWrites"]==0


def test_step10b27_cross_cloud_ceremony_outage_failover_cleanup(monkeypatch):
    _bootstrap(monkeypatch)
    lifecycles={
        "AWS_KMS":FakeLifecycle("AWS_KMS"),
        "GCP_KMS":FakeLifecycle("GCP_KMS"),
        "AZURE_KEY_VAULT":FakeLifecycle("AZURE_KEY_VAULT"),
    }
    result=run_live_cross_cloud_acceptance(
        ["AWS_KMS","GCP_KMS","AZURE_KEY_VAULT"],
        threshold=2,
        actor="10b27-cross-cloud",
        lifecycles=lifecycles,
    )
    assert result["status"]=="PASSED"
    assert result["ceremony"]["ceremony_status"]=="PASSED"
    assert result["ceremony"]["required_provider_threshold"]==2
    assert result["outage_drill"]["failover_status"]=="PASSED"
    assert result["outage_drill"]["primary_failure_observed"] is True
    assert result["outage_drill"]["fallback_signature_verified"] is True
    assert set(result["cleanup"])=={
        "AWS_KMS","GCP_KMS","AZURE_KEY_VAULT"
    }
    assert all(
        x["readback"]["cleanupVerified"] is True
        and x["postCleanupSignBlocked"] is True
        for x in result["cleanup"].values()
    )
    assert result["credentials_persisted"] is False
    assert result["private_key_export_allowed"] is False
    assert result["production_writes"]==0
    assert result["bilibili_writes"]==0

    # The outage path already cleaned the primary. Final cleanup must not
    # repeat an irreversible provider operation.
    assert lifecycles["AWS_KMS"].disable_calls==1


def test_step10b27_fail_closed_without_live_flags(monkeypatch):
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_acceptance_enabled",False
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_cleanup_enabled",False
    )
    with pytest.raises(RuntimeError,match="disabled"):
        run_live_provider_acceptance(
            "AWS_KMS",actor="must-fail",lifecycle=FakeLifecycle("AWS_KMS")
        )


def test_step10b27_sacrificial_prefix_is_mandatory(monkeypatch):
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_allowed_name_prefix",
        "shrimp-sacrificial-",
    )
    require_sacrificial_name("shrimp-sacrificial-good")
    with pytest.raises(RuntimeError,match="outside sacrificial allowlist"):
        require_sacrificial_name("production-root-key")


def test_step10b27_dashboard_does_not_claim_unrun_live_acceptance():
    dashboard=live_cloud_kms_dashboard()
    assert dashboard["credentials_persisted"] is False
    assert dashboard["private_key_export_allowed"] is False
    assert dashboard["automatic_production_writes"] is False
    assert dashboard["live_cloud_account_acceptance_completed"] is False
