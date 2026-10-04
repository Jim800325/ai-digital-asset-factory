from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_quota_operations_ui_and_read_apis_are_available():
    client=TestClient(app)
    page=client.get("/animation/quota")
    assert page.status_code==200
    assert "/review-assets/animation-admin.js" in page.text
    assert "额度运营" in page.text or "QUOTA OPERATIONS" in page.text

    dashboard=client.get("/v1/shrimp-animation/bilibili-quota-dashboard")
    assert dashboard.status_code==200
    payload=dashboard.json()
    assert payload["secrets_redacted"] is True
    assert "accounts" in payload
    assert "stuck_claims" in payload
    assert "reservations_summary" in payload

    assert client.get(
        "/v1/shrimp-animation/bilibili-stuck-claims"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-stuck-reconciliations"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-daily-quota-audits"
    ).status_code==200


def test_stuck_reconcile_requires_independent_live_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_live_acceptance_key",
        "ci-quota-live-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-stuck-claims/"
        "00000000-0000-0000-0000-000000000001/reconcile",
        headers={
            "X-Shrimp-Bilibili-Live-Acceptance-Key":"wrong-key",
        },
        json={"actor":"ci-quota-ops"},
    )
    assert response.status_code==403


def test_daily_quota_audit_endpoint_is_protected(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "ci-quota-monitor-key",
    )
    monkeypatch.setattr(settings,"cron_secret","")
    response=TestClient(app).post(
        "/internal/shrimp-animation/bilibili-daily-quota-audit",
        headers={"X-Shrimp-Health-Monitor-Key":"wrong-key"},
    )
    assert response.status_code==403
