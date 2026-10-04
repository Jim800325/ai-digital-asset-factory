from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.shrimp.bilibili_credentials import (
    run_credential_health_check,
    select_healthy_sacrificial_account,
)


class HealthyProbeAdapter:
    def __init__(self, credentials):
        assert credentials["sessdata"] == "slot-secret-sess"
        assert credentials["bili_jct"] == "slot-secret-jct"
        assert credentials["dede_user_id"] == "88112233"

    def probe_account(self, *, expected_mid: str):
        assert expected_mid == "88112233"
        return {
            "mid":"88112233",
            "uname":"CI Healthy Sacrificial",
            "is_login":True,
            "level":6,
            "publish_probe_ok":True,
        }


class WrongMidProbeAdapter:
    def __init__(self, credentials):
        pass

    def probe_account(self, *, expected_mid: str):
        return {
            "mid":"99999999",
            "uname":"Wrong MID",
            "is_login":True,
            "level":1,
            "publish_probe_ok":True,
        }


def _cleanup():
    with engine.begin() as db:
        db.execute(text("""
          DELETE FROM shrimp_bilibili_health_checks
          WHERE account_id IN (
            SELECT id FROM shrimp_bilibili_accounts
            WHERE account_key LIKE 'ci-health-%'
          )
        """))
        db.execute(text("""
          DELETE FROM shrimp_bilibili_credential_slots
          WHERE account_id IN (
            SELECT id FROM shrimp_bilibili_accounts
            WHERE account_key LIKE 'ci-health-%'
          )
        """))
        db.execute(text("""
          DELETE FROM shrimp_bilibili_accounts
          WHERE account_key LIKE 'ci-health-%'
        """))


def _create_account_and_slot(client: TestClient):
    account=client.post(
        "/v1/shrimp-animation/bilibili-accounts",
        headers={"X-Shrimp-Publish-Key":"ci-health-publish-key"},
        json={
            "account_key":"ci-health-account",
            "display_name":"CI Health Account",
            "mid":"88112233",
            "tags":["ci"],
            "default_tid":122,
            "default_copyright":"ORIGINAL",
            "default_description":"",
            "default_tags":[],
            "cover_strategy":"OPTIONAL",
            "daily_publish_limit":2,
            "timezone":"Asia/Shanghai",
            "safety_policy":{
                "mode":"SACRIFICIAL",
                "require_global_allowlist":True,
                "allow_public_visibility":False,
            },
            "actor":"ci-health",
        },
    )
    assert account.status_code == 201, account.text

    slot=client.post(
        "/v1/shrimp-animation/bilibili-credential-slots",
        headers={"X-Shrimp-Publish-Key":"ci-health-publish-key"},
        json={
            "account_key":"ci-health-account",
            "slot_key":"ci-health-slot",
            "env_prefix":"SHRIMP_BILIBILI_SLOT_CI_HEALTH",
            "actor":"ci-health",
        },
    )
    assert slot.status_code == 201, slot.text
    return account.json(),slot.json()


def test_multi_account_credential_slot_redacts_secret_values(
    monkeypatch,
):
    _cleanup()
    monkeypatch.setattr(
        settings,
        "shrimp_publish_authorization_key",
        "ci-health-publish-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_live_acceptance_key",
        "ci-health-live-key",
    )
    monkeypatch.setenv(
        "SHRIMP_BILIBILI_SLOT_CI_HEALTH_SESSDATA",
        "slot-secret-sess",
    )
    monkeypatch.setenv(
        "SHRIMP_BILIBILI_SLOT_CI_HEALTH_BILI_JCT",
        "slot-secret-jct",
    )
    monkeypatch.setenv(
        "SHRIMP_BILIBILI_SLOT_CI_HEALTH_DEDE_USER_ID",
        "88112233",
    )
    client=TestClient(app)
    try:
        _,slot=_create_account_and_slot(client)
        assert slot["credential_presence"]["required_bundle_present"] is True
        serialized=str(slot)
        assert "slot-secret-sess" not in serialized
        assert "slot-secret-jct" not in serialized

        listing=client.get(
            "/v1/shrimp-animation/bilibili-credential-slots"
        )
        assert listing.status_code == 200
        body=str(listing.json())
        assert "slot-secret-sess" not in body
        assert "slot-secret-jct" not in body
        assert "SHRIMP_BILIBILI_SLOT_CI_HEALTH" in body
    finally:
        _cleanup()


def test_health_probe_mid_match_permission_and_auto_selection(
    monkeypatch,
):
    _cleanup()
    monkeypatch.setattr(
        settings,
        "shrimp_publish_authorization_key",
        "ci-health-publish-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_allowed_account_refs",
        "ci-health-account,88112233,MID:88112233",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_denied_account_refs",
        "prod-main",
    )
    monkeypatch.setenv(
        "SHRIMP_BILIBILI_SLOT_CI_HEALTH_SESSDATA",
        "slot-secret-sess",
    )
    monkeypatch.setenv(
        "SHRIMP_BILIBILI_SLOT_CI_HEALTH_BILI_JCT",
        "slot-secret-jct",
    )
    monkeypatch.setenv(
        "SHRIMP_BILIBILI_SLOT_CI_HEALTH_DEDE_USER_ID",
        "88112233",
    )
    client=TestClient(app)
    try:
        _create_account_and_slot(client)

        result=run_credential_health_check(
            "ci-health-slot",
            actor="ci-health-probe",
            adapter_factory=lambda creds:HealthyProbeAdapter(creds),
        )
        assert result["health_status"] == "HEALTHY"
        assert result["credential_status"] == "CONFIGURED"
        assert result["login_status"] == "LOGGED_IN"
        assert result["mid_status"] == "MATCH"
        assert result["publish_permission_status"] == "ALLOWED"
        assert result["provider_mid"] == "88112233"
        assert result["credential_presence"]["required_bundle_present"] is True

        selected=select_healthy_sacrificial_account()
        assert selected is not None
        assert selected["account_key"] == "ci-health-account"
        assert selected["credential_slot_key"] == "ci-health-slot"
        assert selected["health_status"] == "HEALTHY"

        api_selected=client.get(
            "/v1/shrimp-animation/bilibili-account-selection/healthy"
        )
        assert api_selected.status_code == 200
        assert api_selected.json()["account_key"] == "ci-health-account"
    finally:
        _cleanup()


def test_health_probe_detects_mid_mismatch_and_excludes_account(
    monkeypatch,
):
    _cleanup()
    monkeypatch.setattr(
        settings,
        "shrimp_publish_authorization_key",
        "ci-health-publish-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_allowed_account_refs",
        "ci-health-account,88112233",
    )
    monkeypatch.setenv(
        "SHRIMP_BILIBILI_SLOT_CI_HEALTH_SESSDATA",
        "slot-secret-sess",
    )
    monkeypatch.setenv(
        "SHRIMP_BILIBILI_SLOT_CI_HEALTH_BILI_JCT",
        "slot-secret-jct",
    )
    monkeypatch.setenv(
        "SHRIMP_BILIBILI_SLOT_CI_HEALTH_DEDE_USER_ID",
        "88112233",
    )
    client=TestClient(app)
    try:
        _create_account_and_slot(client)
        result=run_credential_health_check(
            "ci-health-slot",
            actor="ci-health-mismatch",
            adapter_factory=lambda creds:WrongMidProbeAdapter(creds),
        )
        assert result["health_status"] == "UNHEALTHY"
        assert result["mid_status"] == "MISMATCH"
        assert select_healthy_sacrificial_account() is None
    finally:
        _cleanup()


def test_health_check_endpoint_requires_independent_live_key(
    monkeypatch,
):
    _cleanup()
    monkeypatch.setattr(
        settings,
        "shrimp_publish_authorization_key",
        "ci-health-publish-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_live_acceptance_key",
        "ci-health-live-key",
    )
    client=TestClient(app)
    try:
        _create_account_and_slot(client)
        denied=client.post(
            "/v1/shrimp-animation/bilibili-credential-slots/"
            "ci-health-slot/health-check",
            headers={
                "X-Shrimp-Bilibili-Live-Acceptance-Key":"wrong-key",
            },
            json={"actor":"ci"},
        )
        assert denied.status_code == 403
    finally:
        _cleanup()
