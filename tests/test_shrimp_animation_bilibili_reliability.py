from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.providers.animation.shrimp.bilibili_reliability import (
    _error_budget,
    _normalize_root_cause,
)


def test_reliability_ui_and_read_apis_are_available():
    client=TestClient(app)
    page=client.get("/animation/reliability")
    assert page.status_code==200
    assert "RELIABILITY SCORECARD" in page.text
    assert "RECURRENCE DETECTION" in page.text

    dashboard=client.get(
        "/v1/shrimp-animation/bilibili-reliability-dashboard"
    )
    assert dashboard.status_code==200
    body=dashboard.json()
    assert body["secrets_redacted"] is True
    assert "latest_global" in body
    assert "scorecards" in body
    assert "recurrence_clusters" in body

    assert client.get(
        "/v1/shrimp-animation/bilibili-reliability-scorecards"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-recurrence-clusters"
    ).status_code==200


def test_reliability_generation_endpoint_is_protected(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "ci-reliability-monitor-key",
    )
    monkeypatch.setattr(settings,"cron_secret","")
    response=TestClient(app).post(
        "/internal/shrimp-animation/bilibili-reliability-scorecard",
        headers={"X-Shrimp-Health-Monitor-Key":"wrong-key"},
    )
    assert response.status_code==403


def test_root_cause_normalization_is_deterministic():
    a=_normalize_root_cause("  Provider TIMEOUT： upload API  ")
    b=_normalize_root_cause("provider timeout upload api")
    assert a==b


def test_error_budget_uses_strict_slo_math():
    budget=_error_budget(20,1,95.0)
    assert budget["allowed"]==1
    assert budget["consumed"]==1
    assert budget["remaining"]==0
    assert budget["success_rate"]==95.0

    small=_error_budget(1,1,95.0)
    assert small["allowed"]==0
    assert small["remaining"]==0
