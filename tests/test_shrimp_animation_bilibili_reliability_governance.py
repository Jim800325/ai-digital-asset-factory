from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.providers.animation.shrimp.bilibili_reliability_governance import (
    _recommend,
)


def _snapshot(*,score=96.0,burn="HEALTHY",regressions=None,recurrences=None,policies=None):
    return {
        "scorecard":{
            "reliability_score":score,
            "reliability_grade":"A",
        },
        "burn":{"burn_status":burn},
        "open_regressions":regressions or [],
        "recurrence_clusters":recurrences or [],
        "policy_recommendations":policies or [],
    }


def test_reliability_governance_ui_and_read_api():
    client=TestClient(app)
    page=client.get("/animation/reliability-review")
    assert page.status_code==200
    assert "RELIABILITY GOVERNANCE GATE" in page.text
    assert "Authorized Intent ≠ Applied Change" in page.text
    assert "NO APPLY PATH" in page.text

    response=client.get(
        "/v1/shrimp-animation/bilibili-reliability-governance"
    )
    assert response.status_code==200
    body=response.json()
    assert body["human_gate_required"] is True
    assert body["execution_supported"] is False
    assert body["changes_applied"] is False
    assert body["secrets_redacted"] is True


def test_governance_decision_requires_independent_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_governance_key",
        "ci-governance-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_recovery_approval_key",
        "ci-recovery-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-reliability-governance/reviews/"
        "00000000-0000-0000-0000-000000000001/decision",
        headers={
            "X-Shrimp-Bilibili-Reliability-Governance-Key":"wrong",
        },
        json={
            "decision":"ACCEPT_NORMAL",
            "reason":"CI governance gate",
            "actor":"ci",
        },
    )
    assert response.status_code==403


def test_governance_key_cannot_equal_recovery_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_governance_key",
        "same-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_recovery_approval_key",
        "same-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-reliability-governance/reviews/"
        "00000000-0000-0000-0000-000000000001/decision",
        headers={
            "X-Shrimp-Bilibili-Reliability-Governance-Key":"same-key",
        },
        json={
            "decision":"ACCEPT_NORMAL",
            "reason":"CI governance independence",
            "actor":"ci",
        },
    )
    assert response.status_code==503


def test_governance_review_generation_endpoint_is_protected(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "ci-monitor-key",
    )
    monkeypatch.setattr(settings,"cron_secret","")
    response=TestClient(app).post(
        "/internal/shrimp-animation/bilibili-reliability-governance-review",
        headers={"X-Shrimp-Health-Monitor-Key":"wrong"},
    )
    assert response.status_code==403


def test_governance_recommendation_is_deterministic():
    assert _recommend(_snapshot())[0]=="NORMAL"
    assert _recommend(_snapshot(burn="WATCH"))[0]=="CAUTION"
    assert _recommend(_snapshot(
        burn="FAST_BURN"
    ))[0]=="FREEZE_RECOMMENDED"
    assert _recommend(_snapshot(
        regressions=[{"severity":"CRITICAL"}]
    ))[0]=="FREEZE_RECOMMENDED"
    assert _recommend(_snapshot(
        recurrences=[{
            "recurrence_status":"RECURRING",
            "critical_occurrence_count":2,
        }]
    ))[0]=="FREEZE_RECOMMENDED"
