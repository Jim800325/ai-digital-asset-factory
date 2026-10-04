from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.shrimp.bilibili_recovery_policy import (
    account_circuit_allows_reservation,
)


def test_publisher_operations_console_is_available_and_has_no_force_release():
    client=TestClient(app)
    page=client.get("/animation/operations")
    assert page.status_code==200
    assert "PUBLISHER OPERATIONS CONSOLE" in page.text
    assert "Force Release" not in page.text

    console=client.get(
        "/v1/shrimp-animation/bilibili-operations-console"
    )
    assert console.status_code==200
    body=console.json()
    assert body["secrets_redacted"] is True
    assert "open_escalations" in body
    assert "circuits" in body
    assert "recovery_runs" in body

    assert client.get(
        "/v1/shrimp-animation/bilibili-claim-escalations"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-circuit-breakers"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-circuit-events"
    ).status_code==200


def test_recovery_policy_internal_endpoint_is_protected(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "ci-ops-monitor-key",
    )
    monkeypatch.setattr(settings,"cron_secret","")
    response=TestClient(app).post(
        "/internal/shrimp-animation/bilibili-recovery-policy",
        headers={"X-Shrimp-Health-Monitor-Key":"wrong-key"},
    )
    assert response.status_code==403


def test_open_circuit_blocks_new_reservation_candidate(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_publish_authorization_key",
        "ci-ops-publish-key",
    )
    client=TestClient(app)
    key="ci-ops-circuit-account"
    try:
        created=client.post(
            "/v1/shrimp-animation/bilibili-accounts",
            headers={"X-Shrimp-Publish-Key":"ci-ops-publish-key"},
            json={
                "account_key":key,
                "display_name":"CI Ops Circuit",
                "mid":"83000001",
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
                "actor":"ci-ops",
            },
        )
        assert created.status_code==201,created.text
        account_id=created.json()["id"]
        with engine.begin() as db:
            db.execute(text("""
              INSERT INTO shrimp_bilibili_account_circuit_breakers(
                account_id,circuit_status,open_reason,opened_at,
                recovery_not_before,updated_by)
              VALUES(
                CAST(:account_id AS uuid),'OPEN','CI ambiguity',
                now(),now()+interval '1 hour','ci-ops')
            """),{"account_id":account_id})
            assert account_circuit_allows_reservation(
                db,
                account_id,
            ) is False
    finally:
        with engine.begin() as db:
            db.execute(text("""
              DELETE FROM shrimp_bilibili_account_circuit_breakers
              WHERE account_id IN (
                SELECT id FROM shrimp_bilibili_accounts
                WHERE account_key=:key
              )
            """),{"key":key})
            db.execute(text("""
              DELETE FROM shrimp_bilibili_accounts
              WHERE account_key=:key
            """),{"key":key})
