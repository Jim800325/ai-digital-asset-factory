from uuid import uuid4

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app, _production_execution_key_independent


def _payload():
    return {
        "decision": "PROMOTE",
        "reason": "controlled human promotion authorization",
        "actor": "ci-human-production",
        "execution_sha256": "a" * 64,
    }


def test_production_execution_key_must_be_independent(monkeypatch):
    monkeypatch.setattr(settings, "human_approval_key", "approval-key")
    monkeypatch.setattr(settings, "human_release_key", "release-key")
    monkeypatch.setattr(settings, "human_deployment_key", "shared-key")
    monkeypatch.setattr(
        settings,
        "human_production_execution_key",
        "shared-key",
    )

    assert _production_execution_key_independent() is False

    client = TestClient(app)
    response = client.post(
        f"/v1/production-release-executions/{uuid4()}/decision",
        headers={"X-Production-Execution-Key": "shared-key"},
        json=_payload(),
    )
    assert response.status_code == 503
    assert "must be independent" in response.json()["detail"]


def test_production_execution_gate_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(settings, "human_approval_key", "approval-key")
    monkeypatch.setattr(settings, "human_release_key", "release-key")
    monkeypatch.setattr(settings, "human_deployment_key", "deployment-key")
    monkeypatch.setattr(
        settings,
        "human_production_execution_key",
        "production-execution-key",
    )

    assert _production_execution_key_independent() is True

    client = TestClient(app)
    response = client.post(
        f"/v1/production-release-executions/{uuid4()}/decision",
        headers={"X-Production-Execution-Key": "wrong-key"},
        json=_payload(),
    )
    assert response.status_code == 403


def test_production_execution_request_cannot_supply_candidate_id(monkeypatch):
    monkeypatch.setattr(settings, "human_approval_key", "approval-key")
    monkeypatch.setattr(settings, "human_release_key", "release-key")
    monkeypatch.setattr(settings, "human_deployment_key", "deployment-key")
    monkeypatch.setattr(
        settings,
        "human_production_execution_key",
        "production-execution-key",
    )

    payload = _payload()
    payload["candidate_vercel_deployment_id"] = "dpl_attacker_supplied"

    client = TestClient(app)
    response = client.post(
        f"/v1/production-release-executions/{uuid4()}/decision",
        headers={
            "X-Production-Execution-Key": "production-execution-key",
        },
        json=payload,
    )
    assert response.status_code == 422
