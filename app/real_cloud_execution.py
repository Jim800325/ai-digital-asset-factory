from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from app.config import settings

EXPECTED_VERCEL_PROJECT_ID = "prj_orLCRCIm7aVfImH8ihB3gponFOEl"
EXPECTED_VERCEL_OWNER_ID = "team_JO3GTfLCviMWb2pAvSClH0iK"
OIDC_SUBJECT_TOKEN_TYPE = "urn:ietf:params:oauth:token-type:jwt"
GCP_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class RealCloudExecutionBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class SacrificialResource:
    provider: str
    name: str
    locator: dict[str, Any]


def _preview_runtime() -> bool:
    env = (os.getenv("VERCEL_ENV") or "").strip().lower()
    ref = (os.getenv("VERCEL_GIT_COMMIT_REF") or "").strip()
    return env == "preview" or bool(os.getenv("VERCEL") and ref and ref != "main")


def _decode_oidc_claims(token: str | None) -> dict[str, Any] | None:
    value = (token or "").strip()
    if not value:
        return None
    try:
        parts = value.split(".")
        if len(parts) != 3:
            return None
        raw = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw).decode("utf-8"))
        if not isinstance(payload, dict):
            return None
        return {
            "issuer": payload.get("iss"),
            "audience": payload.get("aud"),
            "subject": payload.get("sub"),
            "project_id": payload.get("project_id"),
            "owner_id": payload.get("owner_id"),
            "environment": payload.get("environment"),
        }
    except Exception:
        return None


def _oidc_valid(claims: dict[str, Any] | None) -> bool:
    return bool(
        claims
        and claims.get("project_id") == EXPECTED_VERCEL_PROJECT_ID
        and claims.get("owner_id") == EXPECTED_VERCEL_OWNER_ID
        and claims.get("environment") == "preview"
    )


def _selected_providers() -> list[str]:
    allowed = {"AWS_KMS", "GCP_KMS"}
    values = [
        item.strip().upper()
        for item in settings.real_cloud_selected_providers.split(",")
        if item.strip()
    ]
    return [item for item in values if item in allowed]


def _provider_configured(provider: str) -> bool:
    if provider == "AWS_KMS":
        return bool(
            settings.real_cloud_aws_role_arn.strip()
            and settings.real_cloud_aws_region.strip()
        )
    if provider == "GCP_KMS":
        return bool(
            settings.real_cloud_gcp_workload_identity_audience.strip()
            and settings.real_cloud_gcp_project_id.strip()
            and settings.real_cloud_gcp_location.strip()
            and settings.real_cloud_gcp_key_ring.strip()
        )
    return False


def real_cloud_readiness(oidc_token: str | None = None) -> dict[str, Any]:
    claims = _decode_oidc_claims(oidc_token)
    selected = _selected_providers()
    blockers: list[str] = []
    prefix = settings.real_cloud_allowed_name_prefix.strip()

    if not _preview_runtime():
        blockers.append("PREVIEW_RUNTIME_REQUIRED")
    if not settings.real_cloud_execution_enabled:
        blockers.append("REAL_CLOUD_EXECUTION_ENABLED")
    if not settings.real_cloud_cleanup_enabled:
        blockers.append("REAL_CLOUD_CLEANUP_ENABLED")
    if not settings.real_cloud_execution_key.strip():
        blockers.append("REAL_CLOUD_EXECUTION_KEY")
    if not prefix:
        blockers.append("REAL_CLOUD_ALLOWED_NAME_PREFIX")
    if not _oidc_valid(claims):
        blockers.append("VERCEL_PREVIEW_OIDC_IDENTITY")
    if len(set(selected)) < 2:
        blockers.append("MINIMUM_TWO_PROVIDERS")
    for provider in selected:
        if not _provider_configured(provider):
            blockers.append(provider + "_CONFIGURATION")

    return {
        "step": "10B.27A",
        "status": "READY" if not blockers else "BLOCKED",
        "mode": "REAL_CLOUD_CONTROLLED_ACCEPTANCE",
        "preview_only": True,
        "production_writes": False,
        "bilibili_writes": False,
        "minimum_provider_threshold": 2,
        "selected_providers": selected,
        "sacrificial_name_prefix": prefix or None,
        "execution_enabled": settings.real_cloud_execution_enabled,
        "cleanup_enabled": settings.real_cloud_cleanup_enabled,
        "oidc_identity_valid": _oidc_valid(claims),
        "oidc_identity": claims,
        "providers": [
            {
                "provider": provider,
                "configured": _provider_configured(provider),
                "executable": not blockers and _provider_configured(provider),
            }
            for provider in selected
        ],
        "blockers": sorted(set(blockers)),
        "static_cloud_credentials_required": False,
        "credentials_persisted": False,
        "private_key_export_allowed": False,
        "secrets_redacted": True,
    }


def _require_execution(execution_key: str | None, oidc_token: str | None) -> None:
    readiness = real_cloud_readiness(oidc_token)
    if readiness["status"] != "READY":
        raise RealCloudExecutionBlocked(
            "Real cloud execution is blocked: " + ",".join(readiness["blockers"])
        )
    expected = settings.real_cloud_execution_key.strip()
    if not execution_key or not secrets.compare_digest(execution_key, expected):
        raise PermissionError("Invalid real cloud execution key")


def _sacrificial_name(provider: str) -> str:
    prefix = settings.real_cloud_allowed_name_prefix.strip()
    if not prefix:
        raise RealCloudExecutionBlocked("Sacrificial name prefix is not configured")
    safe = provider.lower().replace("_", "-")
    return f"{prefix}{safe}-{uuid.uuid4().hex[:10]}"


def _assert_sacrificial(name: str) -> None:
    prefix = settings.real_cloud_allowed_name_prefix.strip()
    if not prefix or not name.startswith(prefix):
        raise RealCloudExecutionBlocked("Resource is outside sacrificial allowlist")


def _aws_client(oidc_token: str):
    import boto3
    sts = boto3.client("sts", region_name=settings.real_cloud_aws_region.strip())
    assumed = sts.assume_role_with_web_identity(
        RoleArn=settings.real_cloud_aws_role_arn.strip(),
        RoleSessionName="shrimp-10b27a-preview",
        WebIdentityToken=oidc_token,
        DurationSeconds=900,
    )
    creds = assumed["Credentials"]
    return boto3.client(
        "kms",
        region_name=settings.real_cloud_aws_region.strip(),
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


def _gcp_client(oidc_token: str):
    from google.auth import identity_pool
    from google.auth.transport.requests import Request
    from google.cloud import kms_v1

    class Supplier(identity_pool.SubjectTokenSupplier):
        def get_subject_token(self, context, request) -> str:
            return oidc_token

    credentials = identity_pool.Credentials(
        audience=settings.real_cloud_gcp_workload_identity_audience.strip(),
        subject_token_type=OIDC_SUBJECT_TOKEN_TYPE,
        subject_token_supplier=Supplier(),
        scopes=[GCP_SCOPE],
    )
    service_account = settings.real_cloud_gcp_service_account.strip()
    if service_account:
        from google.auth import impersonated_credentials
        credentials = impersonated_credentials.Credentials(
            source_credentials=credentials,
            target_principal=service_account,
            target_scopes=[GCP_SCOPE],
            lifetime=900,
        )
    credentials.refresh(Request())
    return kms_v1.KeyManagementServiceClient(credentials=credentials)


def _aws_create(client) -> SacrificialResource:
    name = _sacrificial_name("AWS_KMS")
    _assert_sacrificial(name)
    response = client.create_key(
        KeyUsage="SIGN_VERIFY",
        KeySpec="ECC_NIST_P256",
        Description=name,
        Tags=[
            {"TagKey": "shrimp-live-acceptance", "TagValue": "true"},
            {"TagKey": "shrimp-sacrificial-name", "TagValue": name},
        ],
    )
    metadata = response["KeyMetadata"]
    key_id = metadata["KeyId"]
    return SacrificialResource(
        provider="AWS_KMS",
        name=name,
        locator={
            "key_id": key_id,
            "arn": metadata.get("Arn"),
            "region": settings.real_cloud_aws_region.strip(),
        },
    )


def _aws_sign_verify_cleanup(client, resource: SacrificialResource) -> dict[str, Any]:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    _assert_sacrificial(resource.name)
    digest = hashlib.sha256(b"shrimp-step-10b27a-acceptance").digest()
    signed = client.sign(
        KeyId=resource.locator["key_id"],
        Message=digest,
        MessageType="DIGEST",
        SigningAlgorithm="ECDSA_SHA_256",
    )
    public = client.get_public_key(KeyId=resource.locator["key_id"])
    key = serialization.load_der_public_key(public["PublicKey"])
    key.verify(
        signed["Signature"],
        digest,
        ec.ECDSA(utils.Prehashed(hashes.SHA256())),
    )
    client.disable_key(KeyId=resource.locator["key_id"])
    client.schedule_key_deletion(
        KeyId=resource.locator["key_id"],
        PendingWindowInDays=7,
    )
    state = client.describe_key(KeyId=resource.locator["key_id"])["KeyMetadata"]
    blocked = False
    try:
        client.sign(
            KeyId=resource.locator["key_id"],
            Message=digest,
            MessageType="DIGEST",
            SigningAlgorithm="ECDSA_SHA_256",
        )
    except Exception:
        blocked = True
    return {
        "provider": "AWS_KMS",
        "resource_name": resource.name,
        "resource_ref_sha256": hashlib.sha256(
            str(resource.locator.get("arn") or resource.locator["key_id"]).encode()
        ).hexdigest(),
        "sign_verified": True,
        "cleanup_verified": not bool(state.get("Enabled")),
        "readback_state": str(state.get("KeyState")),
        "post_cleanup_sign_blocked": blocked,
    }


def _gcp_create(client) -> SacrificialResource:
    from google.cloud import kms_v1

    name = _sacrificial_name("GCP_KMS")
    _assert_sacrificial(name)
    parent = client.key_ring_path(
        settings.real_cloud_gcp_project_id.strip(),
        settings.real_cloud_gcp_location.strip(),
        settings.real_cloud_gcp_key_ring.strip(),
    )
    key = client.create_crypto_key(
        request={
            "parent": parent,
            "crypto_key_id": name,
            "crypto_key": {
                "purpose": kms_v1.CryptoKey.CryptoKeyPurpose.ASYMMETRIC_SIGN,
                "version_template": {
                    "algorithm": kms_v1.CryptoKeyVersion.CryptoKeyVersionAlgorithm.EC_SIGN_P256_SHA256,
                },
                "labels": {"shrimp-live-acceptance": "true"},
            },
        }
    )
    return SacrificialResource(
        provider="GCP_KMS",
        name=name,
        locator={"crypto_key": key.name, "version": key.name + "/cryptoKeyVersions/1"},
    )


def _gcp_sign_verify_cleanup(client, resource: SacrificialResource) -> dict[str, Any]:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from google.cloud import kms_v1

    _assert_sacrificial(resource.name)
    version = resource.locator["version"]
    digest = hashlib.sha256(b"shrimp-step-10b27a-acceptance").digest()
    signed = client.asymmetric_sign(
        request={"name": version, "digest": {"sha256": digest}}
    )
    public = client.get_public_key(request={"name": version})
    key = serialization.load_pem_public_key(public.pem.encode("utf-8"))
    key.verify(signed.signature, digest, ec.ECDSA(hashes.SHA256()))
    client.update_crypto_key_version(
        request={
            "crypto_key_version": {
                "name": version,
                "state": kms_v1.CryptoKeyVersion.CryptoKeyVersionState.DISABLED,
            },
            "update_mask": {"paths": ["state"]},
        }
    )
    state = client.get_crypto_key_version(request={"name": version})
    blocked = False
    try:
        client.asymmetric_sign(
            request={"name": version, "digest": {"sha256": digest}}
        )
    except Exception:
        blocked = True
    return {
        "provider": "GCP_KMS",
        "resource_name": resource.name,
        "resource_ref_sha256": hashlib.sha256(version.encode()).hexdigest(),
        "sign_verified": True,
        "cleanup_verified": (
            state.state == kms_v1.CryptoKeyVersion.CryptoKeyVersionState.DISABLED
        ),
        "readback_state": str(state.state),
        "post_cleanup_sign_blocked": blocked,
    }


def execute_real_cloud_acceptance(
    *,
    execution_key: str | None,
    oidc_token: str | None,
) -> dict[str, Any]:
    token = (oidc_token or "").strip()
    _require_execution(execution_key, token)

    results: list[dict[str, Any]] = []
    resources: list[SacrificialResource] = []
    try:
        for provider in _selected_providers():
            if provider == "AWS_KMS":
                client = _aws_client(token)
                resource = _aws_create(client)
                resources.append(resource)
                results.append(_aws_sign_verify_cleanup(client, resource))
            elif provider == "GCP_KMS":
                client = _gcp_client(token)
                resource = _gcp_create(client)
                resources.append(resource)
                results.append(_gcp_sign_verify_cleanup(client, resource))
    except Exception as exc:
        raise RuntimeError(
            "Real cloud acceptance failed; manual reconciliation required before retry: "
            + type(exc).__name__
        ) from exc

    passed = (
        len(results) >= 2
        and all(item["sign_verified"] for item in results)
        and all(item["cleanup_verified"] for item in results)
        and all(item["post_cleanup_sign_blocked"] for item in results)
    )
    snapshot = {
        "step": "10B.27A",
        "status": "PASSED" if passed else "FAILED",
        "providers": results,
        "provider_count": len(results),
        "create": passed,
        "sign": passed,
        "verify": passed,
        "cleanup": passed,
        "read_back": passed,
        "post_cleanup_sign_blocked": passed,
        "outage_failover": "PENDING_NEXT_GATE",
        "production_side_effects": 0,
        "bilibili_side_effects": 0,
        "credentials_persisted": False,
    }
    snapshot["evidence_sha256"] = hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return snapshot
