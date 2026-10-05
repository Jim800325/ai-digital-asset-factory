from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed

from app.config import settings
from app.providers.animation.shrimp.bilibili_external_kms import (
    AwsKmsProvider,
    AzureKeyVaultProvider,
    GcpKmsProvider,
)
from app.providers.animation.shrimp.bilibili_external_kms_registry import (
    create_cross_kms_root_ceremony,
    external_kms_dashboard,
    register_external_kms_provider,
    sign_with_failover,
)
from app.providers.animation.shrimp.bilibili_signing_key_lifecycle import (
    bootstrap_signing_trust,
)


class LocalP256:
    def __init__(self):
        self.private=ec.generate_private_key(ec.SECP256R1())

    @property
    def public_der(self):
        return self.private.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    @property
    def public_pem(self):
        return self.private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def sign(self,digest:bytes) -> bytes:
        return self.private.sign(
            digest,
            ec.ECDSA(Prehashed(hashes.SHA256())),
        )

    def verify(self,digest:bytes,signature:bytes) -> bool:
        try:
            self.private.public_key().verify(
                signature,
                digest,
                ec.ECDSA(Prehashed(hashes.SHA256())),
            )
            return True
        except Exception:
            return False


class FakeAwsKmsClient:
    def __init__(self,key:LocalP256):
        self.key=key
        self.sign_calls=[]
        self.verify_calls=[]

    def get_public_key(self,**kwargs):
        return {"PublicKey":self.key.public_der,"KeyId":kwargs["KeyId"]}

    def sign(self,**kwargs):
        self.sign_calls.append(kwargs)
        return {
            "Signature":self.key.sign(kwargs["Message"]),
            "KeyId":kwargs["KeyId"],
        }

    def verify(self,**kwargs):
        self.verify_calls.append(kwargs)
        return {"SignatureValid":self.key.verify(kwargs["Message"],kwargs["Signature"])}


class FakeGcpKmsClient:
    def __init__(self,key:LocalP256):
        self.key=key
        self.sign_requests=[]

    def get_public_key(self,request):
        return SimpleNamespace(pem=self.key.public_pem.decode("utf-8"))

    def asymmetric_sign(self,request):
        self.sign_requests.append(request)
        digest=request["digest"].sha256
        return SimpleNamespace(signature=self.key.sign(digest))


class FakeAzureCryptoClient:
    def __init__(self,key:LocalP256):
        self.local=key
        self.key=None
        self.sign_calls=[]
        self.verify_calls=[]

    def sign(self,algorithm,digest):
        self.sign_calls.append((algorithm,digest))
        return SimpleNamespace(signature=self.local.sign(digest))

    def verify(self,algorithm,digest,signature):
        self.verify_calls.append((algorithm,digest,signature))
        return SimpleNamespace(is_valid=self.local.verify(digest,signature))


class AlwaysUnhealthy:
    provider_type="AWS_KMS"
    provider_ref="aws-kms:unhealthy"

    def health(self):
        return {"healthy":False,"provider_ref":self.provider_ref,"issue":"simulated outage"}


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
    bootstrap_signing_trust(actor="10b26-bootstrap",key_label="tuf-root")


def test_step10b26_official_sdk_adapter_contracts_and_cross_kms_ceremony(monkeypatch):
    _bootstrap(monkeypatch)
    digest=hashlib.sha256(b"step10b26-adapter-contract").digest()

    aws_client=FakeAwsKmsClient(LocalP256())
    gcp_client=FakeGcpKmsClient(LocalP256())
    azure_client=FakeAzureCryptoClient(LocalP256())

    aws=AwsKmsProvider(
        key_id="arn:aws:kms:us-east-1:111122223333:key/test",
        region="us-east-1",
        client=aws_client,
        provider_ref="aws-kms:test",
    )
    gcp=GcpKmsProvider(
        key_version_name="projects/p/locations/global/keyRings/r/cryptoKeys/k/cryptoKeyVersions/1",
        client=gcp_client,
        provider_ref="gcp-kms:test",
    )
    azure=AzureKeyVaultProvider(
        key_id="https://example.vault.azure.net/keys/root/version1",
        client=azure_client,
        provider_ref="azure-key-vault:test",
    )

    aws_sig=aws.sign_digest(digest)
    assert aws.verify_digest(digest,aws_sig) is True
    assert aws_client.sign_calls[0]["MessageType"]=="DIGEST"
    assert aws_client.sign_calls[0]["SigningAlgorithm"]=="ECDSA_SHA_256"

    gcp_sig=gcp.sign_digest(digest)
    assert gcp.verify_digest(digest,gcp_sig) is True
    assert gcp_client.sign_requests[0]["name"].endswith("/cryptoKeyVersions/1")
    assert gcp_client.sign_requests[0]["digest"].sha256==digest

    azure_sig=azure.sign_digest(digest)
    assert azure.verify_digest(digest,azure_sig) is True
    assert azure_client.sign_calls
    assert azure_client.verify_calls

    providers=[
        ("AWS_KMS","aws-kms:test",aws),
        ("GCP_KMS","gcp-kms:test",gcp),
        ("AZURE_KEY_VAULT","azure-key-vault:test",azure),
    ]
    for priority,(ptype,pref,adapter) in enumerate(providers,1):
        info=adapter.key_info()
        register_external_kms_provider(
            provider_type=ptype,
            provider_ref=pref,
            key_locator={"test":True,"providerRef":pref},
            priority=priority,
            actor="10b26-register",
            public_key_pem_b64=info.public_key_pem_b64,
            public_key_fingerprint_sha256=info.public_key_fingerprint_sha256,
        )

    ceremony=create_cross_kms_root_ceremony(
        provider_refs=[x[1] for x in providers],
        threshold=2,
        actor="10b26-cross-kms",
        adapters={x[1]:x[2] for x in providers},
    )
    assert ceremony["ceremony_status"]=="PASSED"
    assert ceremony["required_provider_threshold"]==2
    signatures=ceremony["provider_signatures"]
    assert len(signatures)==3
    assert all(x["verified"] is True for x in signatures)
    assert len({x["providerType"] for x in signatures})==3
    assert ceremony["ceremony_manifest"]["privateKeysExported"] is False


def test_step10b26_failover_skips_unhealthy_provider(monkeypatch):
    _bootstrap(monkeypatch)
    good_client=FakeAwsKmsClient(LocalP256())
    good=AwsKmsProvider(
        key_id="arn:aws:kms:us-east-1:111122223333:key/good",
        region="us-east-1",
        client=good_client,
        provider_ref="aws-kms:good",
    )
    bad=AlwaysUnhealthy()

    register_external_kms_provider(
        provider_type="AWS_KMS",
        provider_ref=bad.provider_ref,
        key_locator={"test":True},
        priority=1,
        actor="10b26-register-bad",
    )
    info=good.key_info()
    register_external_kms_provider(
        provider_type="AWS_KMS",
        provider_ref=good.provider_ref,
        key_locator={"test":True},
        priority=2,
        actor="10b26-register-good",
        public_key_pem_b64=info.public_key_pem_b64,
        public_key_fingerprint_sha256=info.public_key_fingerprint_sha256,
    )

    digest=hashlib.sha256(b"failover").digest()
    signature,run=sign_with_failover(
        digest,
        actor="10b26-failover",
        adapters={bad.provider_ref:bad,good.provider_ref:good},
    )
    assert signature.provider_ref==good.provider_ref
    assert run["failover_status"]=="SUCCEEDED"
    assert run["selected_provider_ref"]==good.provider_ref
    assert run["attempted_provider_refs"]==[bad.provider_ref,good.provider_ref]


def test_step10b26_cross_kms_rejects_same_provider_type_threshold(monkeypatch):
    _bootstrap(monkeypatch)
    a=AwsKmsProvider(
        key_id="key-a",region="us-east-1",
        client=FakeAwsKmsClient(LocalP256()),
        provider_ref="aws-kms:a",
    )
    b=AwsKmsProvider(
        key_id="key-b",region="us-east-1",
        client=FakeAwsKmsClient(LocalP256()),
        provider_ref="aws-kms:b",
    )
    for priority,adapter in enumerate((a,b),1):
        info=adapter.key_info()
        register_external_kms_provider(
            provider_type="AWS_KMS",
            provider_ref=adapter.provider_ref,
            key_locator={"test":True},
            priority=priority,
            actor="register",
            public_key_pem_b64=info.public_key_pem_b64,
            public_key_fingerprint_sha256=info.public_key_fingerprint_sha256,
        )
    with pytest.raises(ValueError,match="distinct provider types"):
        create_cross_kms_root_ceremony(
            provider_refs=[a.provider_ref,b.provider_ref],
            threshold=2,
            actor="must-fail",
            adapters={a.provider_ref:a,b.provider_ref:b},
        )


def test_step10b26_dashboard_safety_flags():
    dashboard=external_kms_dashboard()
    assert dashboard["credentials_persisted"] is False
    assert dashboard["private_key_export_allowed"] is False
    assert dashboard["automatic_production_writes"] is False
    assert dashboard["common_algorithm"]=="ECDSA_P256_SHA256"
