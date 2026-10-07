from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_control_center_homepage_and_summary_are_secret_redacted(
    monkeypatch,
):
    monkeypatch.setattr(
        settings,
        "shrimp_publish_authorization_key",
        "ci-control-center-publish-auth-secret",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_key",
        "ci-control-center-execution-secret",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_live_acceptance_key",
        "ci-control-center-bilibili-live-secret",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_sessdata",
        "ci-control-center-sessdata-secret",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_bili_jct",
        "ci-control-center-jct-secret",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_dede_user_id",
        "123456789",
    )

    client=TestClient(app)

    homepage=client.get("/")
    assert homepage.status_code == 200
    assert "Unified Control Center" in homepage.text
    assert "/review-assets/control-center.js" in homepage.text
    assert "/animation-review" in homepage.text
    assert "/animation-publishing" in homepage.text

    response=client.get(
        "/v1/shrimp-animation/control-center/summary"
    )
    assert response.status_code == 200
    payload=response.json()

    assert payload["mode"] == "READ_ONLY_CONTROL_CENTER"
    assert payload["secrets_redacted"] is True
    assert "system" in payload
    assert "pipeline" in payload
    assert "review" in payload
    assert "publishing" in payload
    assert "bilibili" in payload
    assert "navigation" in payload

    assert "trust_security" in payload
    assert "governance" in payload
    assert "release_track" in payload
    assert "operations" in payload
    assert payload["trust_security"]["secrets_redacted"] is True
    assert payload["release_track"]["step_10b27"]["implemented"] is True
    assert payload["release_track"]["step_10b27"]["integration_accepted"] is True
    assert payload["operations"]["read_only"] is True

    assert payload["system"]["provider"] == "shrimp_animation"
    assert payload["pipeline"]["total_jobs"] >= 0
    assert payload["review"]["total"] >= 0
    assert payload["publishing"]["target_count"] >= 0
    assert payload["bilibili"]["readiness"]["secrets_redacted"] is True

    serialized=str(payload)
    assert "ci-control-center-publish-auth-secret" not in serialized
    assert "ci-control-center-execution-secret" not in serialized
    assert "ci-control-center-bilibili-live-secret" not in serialized
    assert "ci-control-center-sessdata-secret" not in serialized
    assert "ci-control-center-jct-secret" not in serialized


def test_control_center_is_read_only_surface():
    client=TestClient(app)
    response=client.get("/")
    assert response.status_code == 200

    body=response.text
    forbidden_labels=(
        "Upload Now",
        "Publish Now",
        "Delete Now",
        "输入 SESSDATA",
        "输入 bili_jct",
    )
    for label in forbidden_labels:
        assert label not in body


def test_control_center_v02_admin_pages_are_available():
    client=TestClient(app)
    for path, title in (
        ("/animation/accounts","Bilibili 账号"),
        ("/animation/jobs","动画任务中心"),
        ("/animation/executions","发布执行中心"),
        ("/animation/settings","增强设置"),
    ):
        response=client.get(path)
        assert response.status_code == 200
        assert "/review-assets/animation-admin.js" in response.text
        assert "SESSDATA / bili_jct / Keys" in response.text

    homepage=client.get("/")
    assert "/animation/accounts" in homepage.text
    assert "/animation/settings" in homepage.text


def test_admin_surface_does_not_embed_bilibili_secrets():
    client=TestClient(app)
    body=client.get("/animation/accounts").text
    assert "SHRIMP_BILIBILI_SESSDATA" in body
    assert "敏感凭证不会回显" in body
    assert 'id="publishKey"' in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_unified_control_center_v1_homepage_sections_are_present():
    client=TestClient(app)
    response=client.get("/")
    assert response.status_code==200
    body=response.text
    for expected in (
        "Unified Control Center v1.0",
        "SYSTEM STATUS",
        "PRODUCTION PIPELINE",
        "Publishing Readiness",
        "Trust / Signing / HSM / KMS",
        "可靠性与治理",
        "Operations Console",
        "STEP 10B.27A",
    ):
        assert expected in body

    assert "Upload Now" not in body
    assert "Publish Now" not in body
    assert "AWS_SECRET_ACCESS_KEY" not in body
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in body
