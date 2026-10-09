from __future__ import annotations

import base64
import json

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.real_cloud_execution import real_cloud_readiness


def _jwt(payload: dict) -> str:
    def enc(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return enc({"alg": "none", "typ": "JWT"}) + "." + enc(payload) + "."


def _preview_identity() -> str:
    return _jwt({
        "iss": "https://oidc.vercel.com/jim-wus-projects-4bb66217",
        "aud": "https://vercel.com/jim-wus-projects-4bb66217",
        "sub": "owner:jim-wus-projects-4bb66217:project:ai-digital-asset-factory:environment:preview",
        "project_id": "prj_orLCRCIm7aVfImH8ihB3gponFOEl",
        "owner_id": "team_JO3GTfLCviMWb2pAvSClH0iK",
        "environment": "preview",
    })


def test_10b27a_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    result = real_cloud_readiness(_preview_identity())
    assert result["step"] == "10B.27A"
    assert result["status"] == "BLOCKED"
    assert result["preview_only"] is True
    assert result["production_writes"] is False
    assert result["bilibili_writes"] is False
    assert result["credentials_persisted"] is False
    assert result["private_key_export_allowed"] is False
    assert "REAL_CLOUD_EXECUTION_ENABLED" in result["blockers"]
    assert "REAL_CLOUD_CLEANUP_ENABLED" in result["blockers"]
    assert "REAL_CLOUD_EXECUTION_KEY" in result["blockers"]
    assert "REAL_CLOUD_ALLOWED_NAME_PREFIX" in result["blockers"]


def test_10b27a_production_runtime_is_always_blocked(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "production")
    monkeypatch.setattr(settings, "real_cloud_execution_enabled", True)
    monkeypatch.setattr(settings, "real_cloud_cleanup_enabled", True)
    monkeypatch.setattr(settings, "real_cloud_execution_key", "test-key")
    monkeypatch.setattr(settings, "real_cloud_allowed_name_prefix", "shrimp-sacrificial-")
    monkeypatch.setattr(settings, "real_cloud_aws_role_arn", "arn:aws:iam::123:role/test")
    monkeypatch.setattr(settings, "real_cloud_aws_region", "us-east-1")
    monkeypatch.setattr(
        settings,
        "real_cloud_gcp_workload_identity_audience",
        "//iam.googleapis.com/projects/123/locations/global/workloadIdentityPools/vercel/providers/preview",
    )
    monkeypatch.setattr(settings, "real_cloud_gcp_project_id", "preview-project")
    monkeypatch.setattr(settings, "real_cloud_gcp_location", "global")
    monkeypatch.setattr(settings, "real_cloud_gcp_key_ring", "shrimp-10b27a")

    result = real_cloud_readiness(_preview_identity())
    assert result["status"] == "BLOCKED"
    assert "PREVIEW_RUNTIME_REQUIRED" in result["blockers"]


def test_10b27a_readiness_never_returns_execution_key(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setattr(settings, "real_cloud_execution_key", "super-secret-execution-key")
    response = TestClient(app).get(
        "/internal/real-cloud-execution/readiness",
        headers={"X-Vercel-OIDC-Token": _preview_identity()},
    )
    assert response.status_code == 200
    assert "super-secret-execution-key" not in response.text
    assert response.json()["secrets_redacted"] is True


def test_10b27a_execute_is_blocked_by_default(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    response = TestClient(app).post(
        "/internal/real-cloud-execution/execute",
        headers={
            "X-Vercel-OIDC-Token": _preview_identity(),
            "X-Cloud-Execution-Key": "anything",
        },
    )
    assert response.status_code == 409
    assert "Real cloud execution is blocked" in response.json()["detail"]


def test_10b27a_requires_two_selected_providers(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setattr(settings, "real_cloud_selected_providers", "AWS_KMS")
    result = real_cloud_readiness(_preview_identity())
    assert result["status"] == "BLOCKED"
    assert "MINIMUM_TWO_PROVIDERS" in result["blockers"]
