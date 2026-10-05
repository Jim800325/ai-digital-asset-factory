from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from app.config import settings


@dataclass(frozen=True)
class SigningKeyMaterial:
    provider: str
    provider_key_name: str
    provider_key_version: int
    public_key_pem_b64: str
    fingerprint_sha256: str


@dataclass(frozen=True)
class SigningResult:
    signature_b64: str
    key: SigningKeyMaterial
    provider_write_count: int


def _public_material(public: Ed25519PublicKey) -> tuple[str,str]:
    pem=public.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    der=public.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return base64.b64encode(pem).decode("ascii"),hashlib.sha256(der).hexdigest()


def _local_private_key() -> Ed25519PrivateKey:
    raw=settings.shrimp_bilibili_audit_signing_private_key_pem_b64.strip()
    if not raw:
        raise RuntimeError("Bilibili audit signing key is not configured")
    try:
        pem=base64.b64decode(raw.encode("ascii"),validate=True)
        key=serialization.load_pem_private_key(pem,password=None)
    except (ValueError,TypeError) as exc:
        raise ValueError("Invalid base64 PEM audit signing key") from exc
    if not isinstance(key,Ed25519PrivateKey):
        raise ValueError("Audit signing key must be Ed25519")
    return key


def _openbao_headers() -> dict[str,str]:
    token=settings.shrimp_bilibili_openbao_token.strip()
    if not token:
        raise RuntimeError("OpenBao token is not configured")
    return {"X-Vault-Token":token,"Accept":"application/json"}


def _openbao_base() -> str:
    value=settings.shrimp_bilibili_openbao_url.strip().rstrip("/")
    if not value:
        raise RuntimeError("OpenBao URL is not configured")
    return value


def _openbao_path(suffix: str) -> str:
    mount=settings.shrimp_bilibili_openbao_transit_mount.strip().strip("/") or "transit"
    return f"{_openbao_base()}/v1/{mount}/{suffix.lstrip('/')}"


def _openbao_key_name() -> str:
    value=settings.shrimp_bilibili_openbao_key_name.strip()
    if not value:
        raise RuntimeError("OpenBao Transit key name is not configured")
    return value


def _openbao_request(
    method: str,
    suffix: str,
    *,
    json: dict[str,Any] | None=None,
) -> dict[str,Any]:
    timeout=max(1.0,float(settings.shrimp_bilibili_openbao_timeout_seconds))
    with httpx.Client(timeout=timeout,follow_redirects=False) as client:
        response=client.request(
            method,
            _openbao_path(suffix),
            headers=_openbao_headers(),
            json=json,
        )
    if response.status_code>=400:
        raise RuntimeError(
            f"OpenBao Transit request failed: HTTP {response.status_code}"
        )
    body=response.json()
    if not isinstance(body,dict):
        raise RuntimeError("OpenBao Transit returned an invalid response")
    return body


def _openbao_key_material(*,version: int | None=None) -> SigningKeyMaterial:
    name=_openbao_key_name()
    body=_openbao_request("GET",f"keys/{name}")
    data=body.get("data") or {}
    if data.get("type")!="ed25519" or data.get("supports_signing") is not True:
        raise RuntimeError("OpenBao Transit key must be an Ed25519 signing key")
    selected=int(version or data.get("latest_version") or 0)
    keys=data.get("keys") or {}
    item=keys.get(str(selected))
    if not isinstance(item,dict):
        raise RuntimeError("OpenBao Transit public key version is unavailable")
    raw_public=item.get("public_key")
    if not isinstance(raw_public,str) or not raw_public:
        raise RuntimeError("OpenBao Transit did not return an Ed25519 public key")
    try:
        raw=base64.b64decode(raw_public.encode("ascii"),validate=True)
        public=Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError,TypeError) as exc:
        raise RuntimeError("OpenBao Transit returned an invalid Ed25519 public key") from exc
    pem_b64,fingerprint=_public_material(public)
    return SigningKeyMaterial(
        provider="OPENBAO_TRANSIT",
        provider_key_name=name,
        provider_key_version=selected,
        public_key_pem_b64=pem_b64,
        fingerprint_sha256=fingerprint,
    )


def current_signing_key() -> SigningKeyMaterial:
    provider=(settings.shrimp_bilibili_audit_signing_provider or "LOCAL_PEM").strip().upper()
    if provider=="LOCAL_PEM":
        key=_local_private_key()
        pem_b64,fingerprint=_public_material(key.public_key())
        return SigningKeyMaterial(
            provider="LOCAL_PEM",
            provider_key_name="environment",
            provider_key_version=1,
            public_key_pem_b64=pem_b64,
            fingerprint_sha256=fingerprint,
        )
    if provider=="OPENBAO_TRANSIT":
        return _openbao_key_material()
    raise RuntimeError(f"Unsupported audit signing provider: {provider}")



def sign_bytes(payload_bytes: bytes) -> SigningResult:
    key=current_signing_key()
    if key.provider=="LOCAL_PEM":
        signature=_local_private_key().sign(payload_bytes)
        return SigningResult(
            signature_b64=base64.b64encode(signature).decode("ascii"),
            key=key,
            provider_write_count=0,
        )

    body=_openbao_request(
        "POST",
        f"sign/{key.provider_key_name}",
        json={
            "input":base64.b64encode(payload_bytes).decode("ascii"),
            "key_version":key.provider_key_version,
            "hash_algorithm":"none",
        },
    )
    signature=str((body.get("data") or {}).get("signature") or "")
    parts=signature.split(":",2)
    if len(parts)!=3 or parts[0] not in ("vault","bao"):
        raise RuntimeError("OpenBao Transit returned an invalid signature envelope")
    try:
        returned_version=int(parts[1].lstrip("v"))
        raw_signature=base64.b64decode(parts[2].encode("ascii"),validate=True)
    except (ValueError,TypeError) as exc:
        raise RuntimeError("OpenBao Transit signature envelope is invalid") from exc
    if returned_version!=key.provider_key_version:
        raise RuntimeError("OpenBao Transit signed with an unexpected key version")
    return SigningResult(
        signature_b64=base64.b64encode(raw_signature).decode("ascii"),
        key=key,
        provider_write_count=1,
    )


def sign_digest_sha256(digest_sha256: str) -> SigningResult:
    if len(digest_sha256)!=64:
        raise ValueError("Digest must be a SHA-256 hex value")
    key=current_signing_key()
    if key.provider=="LOCAL_PEM":
        signature=_local_private_key().sign(digest_sha256.encode("ascii"))
        return SigningResult(
            signature_b64=base64.b64encode(signature).decode("ascii"),
            key=key,
            provider_write_count=0,
        )

    payload={
        "input":base64.b64encode(digest_sha256.encode("ascii")).decode("ascii"),
        "key_version":key.provider_key_version,
    }
    body=_openbao_request(
        "POST",
        f"sign/{key.provider_key_name}",
        json=payload,
    )
    signature=str((body.get("data") or {}).get("signature") or "")
    parts=signature.split(":",2)
    if len(parts)!=3 or parts[0] not in ("vault","bao"):
        raise RuntimeError("OpenBao Transit returned an invalid signature envelope")
    try:
        returned_version=int(parts[1].lstrip("v"))
        raw_signature=base64.b64decode(parts[2].encode("ascii"),validate=True)
    except (ValueError,TypeError) as exc:
        raise RuntimeError("OpenBao Transit signature envelope is invalid") from exc
    if returned_version!=key.provider_key_version:
        raise RuntimeError("OpenBao Transit signed with an unexpected key version")
    return SigningResult(
        signature_b64=base64.b64encode(raw_signature).decode("ascii"),
        key=key,
        provider_write_count=1,
    )


def rotate_openbao_signing_key() -> SigningKeyMaterial:
    if (settings.shrimp_bilibili_audit_signing_provider or "").strip().upper()!="OPENBAO_TRANSIT":
        raise RuntimeError("OpenBao rotation requires OPENBAO_TRANSIT signing provider")
    if not settings.shrimp_bilibili_openbao_rotation_enabled:
        raise RuntimeError("OpenBao Transit key rotation is disabled")
    before=current_signing_key()
    _openbao_request("POST",f"keys/{before.provider_key_name}/rotate",json={})
    after=current_signing_key()
    if after.provider_key_version<=before.provider_key_version:
        raise RuntimeError("OpenBao Transit key version did not advance after rotation")
    if after.fingerprint_sha256==before.fingerprint_sha256:
        raise RuntimeError("OpenBao Transit rotation did not change the public key")
    return after


def signing_provider_readiness() -> dict[str,Any]:
    provider=(settings.shrimp_bilibili_audit_signing_provider or "LOCAL_PEM").strip().upper()
    configured=False
    issue=None
    key=None
    try:
        key=current_signing_key()
        configured=True
    except (RuntimeError,ValueError) as exc:
        issue=str(exc)
    return {
        "provider":provider,
        "configured":configured,
        "key":(
            {
                "provider":key.provider,
                "provider_key_name":key.provider_key_name,
                "provider_key_version":key.provider_key_version,
                "fingerprint_sha256":key.fingerprint_sha256,
            }
            if key else None
        ),
        "private_key_exported":False if provider=="OPENBAO_TRANSIT" else None,
        "rotation_enabled":bool(
            provider=="OPENBAO_TRANSIT"
            and settings.shrimp_bilibili_openbao_rotation_enabled
        ),
        "issue":issue,
        "secrets_redacted":True,
    }
