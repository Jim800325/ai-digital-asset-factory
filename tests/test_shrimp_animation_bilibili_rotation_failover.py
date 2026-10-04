from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.main import app
from app.providers.animation.shrimp.bilibili_credentials import (
    rotate_credential_slot,
    run_credential_health_check,
    run_health_monitor,
    select_failover_sacrificial_accounts,
    set_slot_selection_priority,
)


class HealthyAdapter:
    def __init__(self, credentials):
        self.credentials=credentials
    def probe_account(self, *, expected_mid: str):
        return {
            "mid":expected_mid,
            "uname":"CI Healthy",
            "is_login":True,
            "level":6,
            "publish_probe_ok":True,
        }


class FailingAdapter:
    def __init__(self, credentials):
        self.credentials=credentials
    def probe_account(self, *, expected_mid: str):
        raise RuntimeError("fixture health failure")


def _cleanup():
    with engine.begin() as db:
        db.execute(text("""
          DELETE FROM shrimp_bilibili_health_monitor_items
          WHERE account_id IN (
            SELECT id FROM shrimp_bilibili_accounts
            WHERE account_key LIKE 'ci-failover-%'
          )
        """))
        db.execute(text("""
          DELETE FROM shrimp_bilibili_health_checks
          WHERE account_id IN (
            SELECT id FROM shrimp_bilibili_accounts
            WHERE account_key LIKE 'ci-failover-%'
          )
        """))
        db.execute(text("""
          DELETE FROM shrimp_bilibili_credential_rotations
          WHERE slot_id IN (
            SELECT id FROM shrimp_bilibili_credential_slots
            WHERE slot_key LIKE 'ci-failover-%'
          )
        """))
        db.execute(text("""
          DELETE FROM shrimp_bilibili_credential_slots
          WHERE slot_key LIKE 'ci-failover-%'
        """))
        db.execute(text("""
          DELETE FROM shrimp_bilibili_accounts
          WHERE account_key LIKE 'ci-failover-%'
        """))


def _seed(client: TestClient, key: str, mid: str, priority: int):
    account_key=f"ci-failover-{key}"
    slot_key=f"ci-failover-{key}"
    env_prefix=f"SHRIMP_BILIBILI_SLOT_{key.upper()}"
    account=client.post(
        "/v1/shrimp-animation/bilibili-accounts",
        headers={"X-Shrimp-Publish-Key":"ci-failover-publish-key"},
        json={
            "account_key":account_key,
            "display_name":f"CI {key}",
            "mid":mid,
            "tags":["ci"],
            "default_tid":122,
            "default_copyright":"ORIGINAL",
            "default_description":"",
            "default_tags":[],
            "cover_strategy":"OPTIONAL",
            "daily_publish_limit":3,
            "timezone":"Asia/Shanghai",
            "safety_policy":{
                "mode":"SACRIFICIAL",
                "require_global_allowlist":True,
                "allow_public_visibility":False,
            },
            "actor":"ci-failover",
        },
    )
    assert account.status_code == 201, account.text
    slot=client.post(
        "/v1/shrimp-animation/bilibili-credential-slots",
        headers={"X-Shrimp-Publish-Key":"ci-failover-publish-key"},
        json={
            "account_key":account_key,
            "slot_key":slot_key,
            "env_prefix":env_prefix,
            "actor":"ci-failover",
        },
    )
    assert slot.status_code == 201, slot.text
    set_slot_selection_priority(
        slot_key,
        selection_priority=priority,
        actor="ci-failover-priority",
    )
    return account_key,slot_key,env_prefix


def test_rotation_requires_new_configured_prefix_and_increments_version(monkeypatch):
    _cleanup()
    monkeypatch.setattr(settings,"shrimp_publish_authorization_key","ci-failover-publish-key")
    client=TestClient(app)
    try:
        account_key,slot_key,prefix=_seed(client,"a","70000001",10)
        monkeypatch.setenv(prefix+"_SESSDATA","old-sess")
        monkeypatch.setenv(prefix+"_BILI_JCT","old-jct")
        monkeypatch.setenv(prefix+"_DEDE_USER_ID","70000001")
        new_prefix="SHRIMP_BILIBILI_SLOT_A_V2"
        monkeypatch.setenv(new_prefix+"_SESSDATA","new-sess")
        monkeypatch.setenv(new_prefix+"_BILI_JCT","new-jct")
        monkeypatch.setenv(new_prefix+"_DEDE_USER_ID","70000001")

        result=rotate_credential_slot(
            slot_key,
            new_env_prefix=new_prefix,
            reason="CI credential rotation",
            actor="ci-rotation",
        )
        assert result["previous_version"] == 1
        assert result["new_version"] == 2
        assert result["new_env_prefix"] == new_prefix
        assert result["secrets_redacted"] is True
        serialized=str(result)
        assert "new-sess" not in serialized
        assert "new-jct" not in serialized
    finally:
        _cleanup()


def test_consecutive_failures_quarantine_then_success_recovers(monkeypatch):
    _cleanup()
    monkeypatch.setattr(settings,"shrimp_publish_authorization_key","ci-failover-publish-key")
    monkeypatch.setattr(settings,"shrimp_bilibili_health_failure_threshold",2)
    client=TestClient(app)
    try:
        _,slot_key,prefix=_seed(client,"b","70000002",20)
        monkeypatch.setenv(prefix+"_SESSDATA","sess")
        monkeypatch.setenv(prefix+"_BILI_JCT","jct")
        monkeypatch.setenv(prefix+"_DEDE_USER_ID","70000002")

        first=run_credential_health_check(
            slot_key,actor="ci-fail-1",
            adapter_factory=lambda creds:FailingAdapter(creds),
        )
        assert first["degradation_status"] == "DEGRADED"
        assert first["consecutive_failures"] == 1

        second=run_credential_health_check(
            slot_key,actor="ci-fail-2",
            adapter_factory=lambda creds:FailingAdapter(creds),
        )
        assert second["degradation_status"] == "QUARANTINED"
        assert second["consecutive_failures"] == 2

        recovered=run_credential_health_check(
            slot_key,actor="ci-recover",
            adapter_factory=lambda creds:HealthyAdapter(creds),
        )
        assert recovered["health_status"] == "HEALTHY"
        assert recovered["degradation_status"] == "NORMAL"
        assert recovered["consecutive_failures"] == 0
    finally:
        _cleanup()


def test_failover_priority_and_monitor_selection(monkeypatch):
    _cleanup()
    monkeypatch.setattr(settings,"shrimp_publish_authorization_key","ci-failover-publish-key")
    monkeypatch.setattr(
        settings,
        "shrimp_publish_execution_allowed_account_refs",
        "ci-failover-a,70000001,ci-failover-b,70000002",
    )
    client=TestClient(app)
    try:
        a,slot_a,prefix_a=_seed(client,"a","70000001",50)
        b,slot_b,prefix_b=_seed(client,"b","70000002",10)
        for prefix,mid in ((prefix_a,"70000001"),(prefix_b,"70000002")):
            monkeypatch.setenv(prefix+"_SESSDATA","sess-"+mid)
            monkeypatch.setenv(prefix+"_BILI_JCT","jct-"+mid)
            monkeypatch.setenv(prefix+"_DEDE_USER_ID",mid)

        run_credential_health_check(
            slot_a,actor="ci-a",
            adapter_factory=lambda creds:HealthyAdapter(creds),
        )
        run_credential_health_check(
            slot_b,actor="ci-b",
            adapter_factory=lambda creds:HealthyAdapter(creds),
        )
        candidates=select_failover_sacrificial_accounts(limit=2)
        assert [x["account_key"] for x in candidates] == [b,a]

        monitor=run_health_monitor(
            actor="ci-monitor",
            adapter_factory=lambda creds:HealthyAdapter(creds),
        )
        assert monitor["monitor_status"] == "SUCCEEDED"
        assert monitor["healthy_slot_count"] == 2
        assert monitor["selected_account_key"] == b
        assert monitor["selected_slot_key"] == slot_b
        assert monitor["secrets_redacted"] is True
    finally:
        _cleanup()


def test_scheduled_monitor_endpoint_is_protected(monkeypatch):
    monkeypatch.setattr(settings,"shrimp_bilibili_health_monitor_key","ci-monitor-key")
    response=TestClient(app).post(
        "/internal/shrimp-animation/bilibili-health-monitor",
        headers={"X-Shrimp-Health-Monitor-Key":"wrong"},
    )
    assert response.status_code == 403
