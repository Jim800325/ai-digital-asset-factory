from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_incident_ops_ui_and_read_apis_are_available():
    client=TestClient(app)
    page=client.get("/animation/operations")
    assert page.status_code==200
    assert "ON-CALL ROUTING" in page.text
    assert "RECOVERY SLA" in page.text
    assert "POST-INCIDENT REVIEW" in page.text
    assert "CORRECTIVE ACTIONS" in page.text

    assert client.get(
        "/v1/shrimp-animation/bilibili-incident-ops-summary"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-oncall-routes"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-incident-sla-events"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-post-incident-reviews"
    ).status_code==200
    assert client.get(
        "/v1/shrimp-animation/bilibili-corrective-actions"
    ).status_code==200


def test_incident_ack_requires_independent_ops_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_incident_ops_key",
        "ci-incident-ops-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_recovery_approval_key",
        "ci-recovery-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-incidents/"
        "00000000-0000-0000-0000-000000000001/acknowledge",
        headers={"X-Shrimp-Bilibili-Incident-Ops-Key":"wrong"},
        json={"actor":"ci"},
    )
    assert response.status_code==403


def test_incident_ops_key_cannot_equal_recovery_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_incident_ops_key",
        "same-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_recovery_approval_key",
        "same-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-incidents/"
        "00000000-0000-0000-0000-000000000001/acknowledge",
        headers={"X-Shrimp-Bilibili-Incident-Ops-Key":"same-key"},
        json={"actor":"ci"},
    )
    assert response.status_code==503
