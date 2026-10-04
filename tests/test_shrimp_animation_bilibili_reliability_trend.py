from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.providers.animation.shrimp.bilibili_reliability_trend import (
    _burn,
)


def test_reliability_trend_ui_and_read_apis():
    client=TestClient(app)
    page=client.get("/animation/reliability")
    assert page.status_code==200
    assert "RELIABILITY TREND" in page.text
    assert "ERROR BUDGET BURN" in page.text
    assert "REGRESSION DETECTION" in page.text
    assert "POLICY RECOMMENDATIONS" in page.text

    dashboard=client.get(
        "/v1/shrimp-animation/bilibili-reliability-trend-dashboard"
    )
    assert dashboard.status_code==200
    body=dashboard.json()
    assert body["secrets_redacted"] is True
    assert body["observe_only"] is True
    assert "trend_points" in body
    assert "burn_evaluations" in body
    assert "open_regressions" in body
    assert "recommendations" in body

    assert client.get(
        "/v1/shrimp-animation/bilibili-reliability-trends"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-error-budget-burn"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-reliability-regressions"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-reliability-policy-recommendations"
    ).status_code==200


def test_reliability_analysis_endpoint_is_protected(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "ci-reliability-analysis-key",
    )
    monkeypatch.setattr(settings,"cron_secret","")
    response=TestClient(app).post(
        "/internal/shrimp-animation/bilibili-reliability-analysis",
        headers={"X-Shrimp-Health-Monitor-Key":"wrong"},
    )
    assert response.status_code==403


def test_error_budget_burn_rate_math():
    assert _burn(0,20,95.0)==0.0
    assert _burn(1,20,95.0)==1.0
    assert _burn(2,20,95.0)==2.0
    assert _burn(1,0,95.0)==0.0
