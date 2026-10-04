from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.providers.animation.shrimp.bilibili_reliability_policy_change import (
    _proposed_snapshot,
)


def test_policy_change_ui_and_read_api():
    client=TestClient(app)
    page=client.get("/animation/reliability-review")
    assert page.status_code==200
    assert "CONTROLLED CHANGE PLAN" in page.text
    assert "Second Human Apply Decisions" in page.text

    response=client.get(
        "/v1/shrimp-animation/bilibili-reliability-policy-change"
    )
    assert response.status_code==200
    body=response.json()
    assert body["second_human_gate_required"] is True
    assert body["dry_run_required"] is True
    assert body["provider_writes"] is False
    assert body["secrets_redacted"] is True
    control=body["policy_control"]
    assert control["automation_exposure"] in {"NORMAL","CAUTION","FROZEN"}


def test_policy_apply_gate_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_policy_apply_key",
        "ci-policy-apply-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_governance_key",
        "ci-governance-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-reliability-change-plans/"
        "00000000-0000-0000-0000-000000000001/decision",
        headers={
            "X-Shrimp-Bilibili-Reliability-Policy-Apply-Key":"wrong",
        },
        json={
            "decision":"APPLY",
            "reason":"CI second human gate",
            "actor":"ci",
            "plan_sha256":"0"*64,
            "dry_run_sha256":"1"*64,
        },
    )
    assert response.status_code==403


def test_policy_apply_key_must_be_independent(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_policy_apply_key",
        "same-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_governance_key",
        "same-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-reliability-change-plans/"
        "00000000-0000-0000-0000-000000000001/decision",
        headers={
            "X-Shrimp-Bilibili-Reliability-Policy-Apply-Key":"same-key",
        },
        json={
            "decision":"APPLY",
            "reason":"CI key independence",
            "actor":"ci",
            "plan_sha256":"0"*64,
            "dry_run_sha256":"1"*64,
        },
    )
    assert response.status_code==503


def test_change_plan_mapping_is_deterministic():
    current={
        "control_key":"GLOBAL",
        "automation_exposure":"NORMAL",
        "quota_multiplier_percent":100,
        "new_reservation_allowed":True,
        "control_version":7,
    }
    caution=_proposed_snapshot(current,"CAUTION_CONTROLS")
    assert caution["automation_exposure"]=="CAUTION"
    assert caution["quota_multiplier_percent"]==50
    assert caution["new_reservation_allowed"] is True
    assert caution["control_version"]==8

    freeze=_proposed_snapshot(current,"FREEZE_CHANGE_INTENT")
    assert freeze["automation_exposure"]=="FROZEN"
    assert freeze["quota_multiplier_percent"]==0
    assert freeze["new_reservation_allowed"] is False
    assert freeze["control_version"]==8
