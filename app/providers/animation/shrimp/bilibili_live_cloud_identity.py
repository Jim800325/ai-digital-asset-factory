from __future__ import annotations

import base64
import hashlib
import json
import os
from typing import Any

from app.config import settings


OIDC_SUBJECT_TOKEN_TYPE="urn:ietf:params:oauth:token-type:jwt"
GCP_SCOPE="https://www.googleapis.com/auth/cloud-platform"
EXPECTED_VERCEL_PROJECT_ID="prj_orLCRCIm7aVfImH8ihB3gponFOEl"
EXPECTED_VERCEL_OWNER_ID="team_JO3GTfLCviMWb2pAvSClH0iK"


def _runtime_oidc_token(explicit_token: str | None=None) -> str:
    if (os.getenv("VERCEL_ENV") or "").strip().lower()!="preview":
        return ""
    value=(explicit_token or "").strip()
    if value:
        return value
    try:
        from vercel.oidc import get_vercel_oidc_token_sync
        value=(get_vercel_oidc_token_sync() or "").strip()
        if value:
            return value
    except Exception:
        pass
    try:
        from vercel.functions import get_env
        runtime_env=get_env()
        value=(getattr(runtime_env,"VERCEL_OIDC_TOKEN","") or "").strip()
        if value:
            return value
    except Exception:
        pass
    return (os.getenv("VERCEL_OIDC_TOKEN") or "").strip()


def _require_oidc_token(explicit_token: str | None=None) -> str:
    if not settings.shrimp_bilibili_live_cloud_kms_oidc_enabled:
        raise RuntimeError("Live Cloud KMS OIDC federation is disabled")
    token=_runtime_oidc_token(explicit_token)
    if not token:
        raise RuntimeError("Vercel Preview OIDC token is unavailable")
    return token


def _safe_oidc_identity(explicit_token: str | None=None) -> dict[str,Any] | None:
    token=_runtime_oidc_token(explicit_token)
    if not token:
        return None
    try:
        parts=token.split(".")
        if len(parts)!=3:
            return None
        raw=parts[1]+"="*(-len(parts[1])%4)
        payload=json.loads(
            base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8")
        )
        if not isinstance(payload,dict):
            return None
        return {
            "issuer":payload.get("iss"),
            "audience":payload.get("aud"),
            "subject":payload.get("sub"),
            "project_id":payload.get("project_id"),
            "owner_id":payload.get("owner_id"),
            "environment":payload.get("environment"),
        }
    except Exception:
        return None


def _masked_ref(value: str) -> str | None:
    value=(value or "").strip()
    if not value:
        return None
    digest=hashlib.sha256(value.encode("utf-8")).hexdigest()
    return "sha256:"+digest[:16]


def live_cloud_identity_readiness(oidc_token: str | None=None) -> dict[str,Any]:
    token_present=bool(_runtime_oidc_token(oidc_token))
    oidc_identity=_safe_oidc_identity(oidc_token)
    oidc_identity_valid=bool(
        oidc_identity
        and oidc_identity.get("environment")=="preview"
        and oidc_identity.get("project_id")==EXPECTED_VERCEL_PROJECT_ID
        and oidc_identity.get("owner_id")==EXPECTED_VERCEL_OWNER_ID
    )
    flags_ready=(
        oidc_identity_valid
        and
        settings.shrimp_bilibili_live_cloud_kms_oidc_enabled
        and settings.shrimp_bilibili_live_cloud_kms_acceptance_enabled
        and settings.shrimp_bilibili_live_cloud_kms_cleanup_enabled
    )
    prefix=settings.shrimp_bilibili_live_cloud_kms_allowed_name_prefix.strip()

    aws={
        "provider_type":"AWS_KMS",
        "identity_mode":"VERCEL_OIDC_AWS_STS",
        "configured":bool(
            settings.shrimp_bilibili_live_aws_role_arn.strip()
            and settings.shrimp_bilibili_live_aws_region.strip()
        ),
        "role_arn_present":bool(
            settings.shrimp_bilibili_live_aws_role_arn.strip()
        ),
        "role_arn_ref":_masked_ref(
            settings.shrimp_bilibili_live_aws_role_arn
        ),
        "region":settings.shrimp_bilibili_live_aws_region.strip() or None,
    }
    gcp={
        "provider_type":"GCP_KMS",
        "identity_mode":"VERCEL_OIDC_GCP_WIF",
        "configured":bool(
            settings.shrimp_bilibili_live_gcp_workload_identity_audience.strip()
            and settings.shrimp_bilibili_live_gcp_project_id.strip()
            and settings.shrimp_bilibili_live_gcp_location.strip()
            and settings.shrimp_bilibili_live_gcp_key_ring.strip()
        ),
        "workload_identity_audience_present":bool(
            settings.shrimp_bilibili_live_gcp_workload_identity_audience.strip()
        ),
        "workload_identity_audience_ref":_masked_ref(
            settings.shrimp_bilibili_live_gcp_workload_identity_audience
        ),
        "service_account_impersonation":bool(
            settings.shrimp_bilibili_live_gcp_service_account.strip()
        ),
        "service_account_ref":_masked_ref(
            settings.shrimp_bilibili_live_gcp_service_account
        ),
        "project_id":settings.shrimp_bilibili_live_gcp_project_id.strip() or None,
        "location":settings.shrimp_bilibili_live_gcp_location.strip() or None,
        "key_ring":settings.shrimp_bilibili_live_gcp_key_ring.strip() or None,
    }
    azure={
        "provider_type":"AZURE_KEY_VAULT",
        "identity_mode":"DEFAULT_AZURE_CREDENTIAL",
        "configured":bool(
            settings.shrimp_bilibili_live_azure_vault_url.strip()
        ),
        "vault_url_present":bool(
            settings.shrimp_bilibili_live_azure_vault_url.strip()
        ),
        "vault_url_ref":_masked_ref(
            settings.shrimp_bilibili_live_azure_vault_url
        ),
        "selected_for_initial_10b27a":False,
    }

    providers=[aws,gcp,azure]
    for item in providers:
        item["executable"]=bool(
            flags_ready
            and token_present
            and prefix
            and item["configured"]
            and (
                item["provider_type"]!="AZURE_KEY_VAULT"
                or item["selected_for_initial_10b27a"]
            )
        )

    blockers=[]
    if (os.getenv("VERCEL_ENV") or "").strip().lower()!="preview":
        blockers.append("VERCEL_ENV_PREVIEW_REQUIRED")
    if not token_present:
        blockers.append("VERCEL_OIDC_TOKEN")
    elif not oidc_identity_valid:
        blockers.append("VERCEL_OIDC_IDENTITY_MISMATCH")
    if not settings.shrimp_bilibili_live_cloud_kms_oidc_enabled:
        blockers.append("SHRIMP_BILIBILI_LIVE_CLOUD_KMS_OIDC_ENABLED")
    if not settings.shrimp_bilibili_live_cloud_kms_acceptance_enabled:
        blockers.append("SHRIMP_BILIBILI_LIVE_CLOUD_KMS_ACCEPTANCE_ENABLED")
    if not settings.shrimp_bilibili_live_cloud_kms_cleanup_enabled:
        blockers.append("SHRIMP_BILIBILI_LIVE_CLOUD_KMS_CLEANUP_ENABLED")
    if not prefix:
        blockers.append("SHRIMP_BILIBILI_LIVE_CLOUD_KMS_ALLOWED_NAME_PREFIX")
    if not aws["role_arn_present"]:
        blockers.append("SHRIMP_BILIBILI_LIVE_AWS_ROLE_ARN")
    if not aws["region"]:
        blockers.append("SHRIMP_BILIBILI_LIVE_AWS_REGION")
    if not gcp["workload_identity_audience_present"]:
        blockers.append(
            "SHRIMP_BILIBILI_LIVE_GCP_WORKLOAD_IDENTITY_AUDIENCE"
        )
    if not gcp["project_id"]:
        blockers.append("SHRIMP_BILIBILI_LIVE_GCP_PROJECT_ID")
    if not gcp["location"]:
        blockers.append("SHRIMP_BILIBILI_LIVE_GCP_LOCATION")
    if not gcp["key_ring"]:
        blockers.append("SHRIMP_BILIBILI_LIVE_GCP_KEY_RING")

    selected=[aws,gcp]
    selected_ready=all(x["executable"] for x in selected)
    return {
        "step":"10B.27A",
        "status":"READY" if selected_ready else "BLOCKED",
        "identity_strategy":"VERCEL_OIDC_SHORT_LIVED",
        "selected_provider_types":["AWS_KMS","GCP_KMS"],
        "minimum_provider_threshold":2,
        "vercel_env":(os.getenv("VERCEL_ENV") or "").strip().lower() or None,
        "oidc_token_present":token_present,
        "oidc_identity_valid":oidc_identity_valid,
        "oidc_identity":oidc_identity,
        "live_acceptance_enabled":
            settings.shrimp_bilibili_live_cloud_kms_acceptance_enabled,
        "cleanup_verification_enabled":
            settings.shrimp_bilibili_live_cloud_kms_cleanup_enabled,
        "oidc_federation_enabled":
            settings.shrimp_bilibili_live_cloud_kms_oidc_enabled,
        "sacrificial_name_prefix":prefix or None,
        "providers":providers,
        "blockers":sorted(set(blockers)),
        "credentials_persisted":False,
        "static_cloud_credentials_required":False,
        "production_writes":False,
        "bilibili_writes":False,
        "secrets_redacted":True,
    }


def aws_kms_client_from_vercel_oidc(oidc_token: str | None=None) -> Any:
    role_arn=settings.shrimp_bilibili_live_aws_role_arn.strip()
    region=settings.shrimp_bilibili_live_aws_region.strip()
    if not role_arn or not region:
        raise RuntimeError("Live AWS OIDC role/region is not configured")
    token=_require_oidc_token(oidc_token)

    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    sts=boto3.client(
        "sts",
        region_name=region,
        config=Config(signature_version=UNSIGNED),
    )
    response=sts.assume_role_with_web_identity(
        RoleArn=role_arn,
        RoleSessionName="shrimp-10b27a-preview",
        WebIdentityToken=token,
        DurationSeconds=900,
    )
    creds=response["Credentials"]
    return boto3.client(
        "kms",
        region_name=region,
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
    )


class _VercelOidcSubjectTokenSupplier:
    def __init__(self,token: str):
        self._token=token

    def get_subject_token(self,context,request) -> str:
        return self._token


def gcp_kms_client_from_vercel_oidc(oidc_token: str | None=None) -> Any:
    audience=(
        settings.shrimp_bilibili_live_gcp_workload_identity_audience.strip()
    )
    if not audience:
        raise RuntimeError("Live GCP Workload Identity audience is not configured")
    token=_require_oidc_token(oidc_token)

    from google.auth import identity_pool
    from google.auth.transport.requests import Request
    from google.cloud import kms_v1

    class Supplier(identity_pool.SubjectTokenSupplier):
        def get_subject_token(self,context,request) -> str:
            return token

    credentials=identity_pool.Credentials(
        audience=audience,
        subject_token_type=OIDC_SUBJECT_TOKEN_TYPE,
        subject_token_supplier=Supplier(),
        scopes=[GCP_SCOPE],
    )
    service_account=(
        settings.shrimp_bilibili_live_gcp_service_account.strip()
    )
    if service_account:
        from google.auth import impersonated_credentials
        credentials=impersonated_credentials.Credentials(
            source_credentials=credentials,
            target_principal=service_account,
            target_scopes=[GCP_SCOPE],
            lifetime=900,
        )
    credentials.refresh(Request())
    return kms_v1.KeyManagementServiceClient(credentials=credentials)
