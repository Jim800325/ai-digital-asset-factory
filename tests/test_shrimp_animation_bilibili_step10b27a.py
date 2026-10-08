from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.providers.animation.shrimp import bilibili_live_cloud_identity as identity
from app.providers.animation.shrimp import bilibili_live_cloud_kms as live


def _configure_ready(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV","preview")
    monkeypatch.setenv("VERCEL_OIDC_TOKEN","ci-vercel-oidc-token")
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_oidc_enabled",True
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_acceptance_enabled",True
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_cleanup_enabled",True
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_allowed_name_prefix",
        "shrimp-sacrificial-",
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_aws_role_arn",
        "arn:aws:iam::123456789012:role/shrimp-10b27a",
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_aws_region","us-east-1"
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_gcp_workload_identity_audience",
        "//iam.googleapis.com/projects/123/locations/global/"
        "workloadIdentityPools/vercel/providers/preview",
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_gcp_project_id","shrimp-preview"
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_gcp_location","global"
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_gcp_key_ring","shrimp-10b27a"
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_gcp_service_account",
        "shrimp-10b27a@shrimp-preview.iam.gserviceaccount.com",
    )


def test_step10b27a_readiness_blocks_without_cloud_identity(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV","preview")
    monkeypatch.setenv("VERCEL_OIDC_TOKEN","ci-vercel-oidc-token")
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_oidc_enabled",False
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_acceptance_enabled",False
    )
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_cloud_kms_cleanup_enabled",False
    )
    monkeypatch.setattr(settings,"shrimp_bilibili_live_aws_role_arn","")
    monkeypatch.setattr(settings,"shrimp_bilibili_live_aws_region","")
    monkeypatch.setattr(
        settings,"shrimp_bilibili_live_gcp_workload_identity_audience",""
    )
    monkeypatch.setattr(settings,"shrimp_bilibili_live_gcp_project_id","")
    monkeypatch.setattr(settings,"shrimp_bilibili_live_gcp_location","")
    monkeypatch.setattr(settings,"shrimp_bilibili_live_gcp_key_ring","")

    result=identity.live_cloud_identity_readiness()
    assert result["status"]=="BLOCKED"
    assert result["oidc_token_present"] is True
    assert result["static_cloud_credentials_required"] is False
    assert result["credentials_persisted"] is False
    assert "SHRIMP_BILIBILI_LIVE_AWS_ROLE_ARN" in result["blockers"]
    assert (
        "SHRIMP_BILIBILI_LIVE_GCP_WORKLOAD_IDENTITY_AUDIENCE"
        in result["blockers"]
    )
    serialized=str(result)
    assert "ci-vercel-oidc-token" not in serialized


def test_step10b27a_readiness_selects_aws_and_gcp(monkeypatch):
    _configure_ready(monkeypatch)
    result=identity.live_cloud_identity_readiness()
    assert result["status"]=="READY"
    assert result["selected_provider_types"]==["AWS_KMS","GCP_KMS"]
    assert result["minimum_provider_threshold"]==2
    selected=[
        item for item in result["providers"]
        if item["provider_type"] in result["selected_provider_types"]
    ]
    assert all(item["executable"] is True for item in selected)
    assert result["blockers"]==[]
    assert "ci-vercel-oidc-token" not in str(result)


def test_step10b27a_aws_lifecycle_uses_oidc_client(monkeypatch):
    _configure_ready(monkeypatch)
    fake=object()
    monkeypatch.setattr(live,"aws_kms_client_from_vercel_oidc",lambda _token=None:fake)
    lifecycle=live.AwsLiveKmsLifecycle()
    assert lifecycle.client is fake
    assert lifecycle.region=="us-east-1"


def test_step10b27a_gcp_lifecycle_uses_oidc_client(monkeypatch):
    _configure_ready(monkeypatch)
    fake=object()
    monkeypatch.setattr(live,"gcp_kms_client_from_vercel_oidc",lambda _token=None:fake)
    lifecycle=live.GcpLiveKmsLifecycle()
    assert lifecycle.client is fake
    assert lifecycle.project=="shrimp-preview"
    assert lifecycle.key_ring=="shrimp-10b27a"


def test_step10b27a_readiness_api_never_returns_token(monkeypatch):
    _configure_ready(monkeypatch)
    response=TestClient(app).get(
        "/v1/shrimp-animation/bilibili-live-cloud-kms/readiness"
    )
    assert response.status_code==200
    body=response.json()
    assert body["status"]=="READY"
    assert body["secrets_redacted"] is True
    assert body["credentials_persisted"] is False
    assert "ci-vercel-oidc-token" not in response.text


def _unsigned_test_jwt(payload):
    import base64
    import json

    def encode(value):
        raw=json.dumps(value,separators=(",",":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return encode({"alg":"none","typ":"JWT"})+"."+encode(payload)+"."


def test_step10b27a_request_oidc_header_overrides_ambient_token(monkeypatch):
    _configure_ready(monkeypatch)
    monkeypatch.setenv(
        "VERCEL_OIDC_TOKEN",
        _unsigned_test_jwt({
            "iss":"https://oidc.vercel.com/local",
            "aud":"https://vercel.com/local",
            "sub":"owner:local:project:local:environment:development",
            "environment":"development",
        }),
    )
    request_token=_unsigned_test_jwt({
        "iss":"https://oidc.vercel.com/team",
        "aud":"https://vercel.com/team",
        "sub":"owner:team:project:app:environment:preview",
        "project_id":"prj_test",
        "owner_id":"team_test",
        "environment":"preview",
    })

    response=TestClient(app).get(
        "/v1/shrimp-animation/bilibili-live-cloud-kms/readiness",
        headers={"X-Vercel-OIDC-Token":request_token},
    )
    assert response.status_code==200
    identity_body=response.json()["oidc_identity"]
    assert identity_body["environment"]=="preview"
    assert identity_body["subject"].endswith(":environment:preview")
    assert request_token not in response.text
