from uuid import uuid4

from fastapi.testclient import TestClient

import app.main as main
from app.config import settings


def _keys(monkeypatch):
    monkeypatch.setenv("VERCEL_ENV", "preview")
    monkeypatch.setattr(settings, "deployment_authorization_preview_only", True)
    monkeypatch.setattr(settings, "preview_acceptance_key", "preview-key")
    monkeypatch.setattr(settings, "human_approval_key", "approval-key")
    monkeypatch.setattr(settings, "human_release_key", "release-key")
    monkeypatch.setattr(settings, "human_deployment_key", "deployment-key")
    monkeypatch.setattr(
        settings,
        "human_production_execution_key",
        "production-execution-key",
    )


def test_step4a_readiness_is_read_only(monkeypatch):
    monkeypatch.setattr(
        main,
        "vercel_prepare_acceptance_readiness",
        lambda: {
            "status": "NOT_READY",
            "provider_write_performed": False,
            "production_traffic_changed": False,
        },
    )
    client = TestClient(main.app)
    response = client.get(
        "/internal/vercel-prepare-acceptance/readiness"
    )
    assert response.status_code == 200
    assert response.json()["provider_write_performed"] is False
    assert response.json()["production_traffic_changed"] is False


def test_step4a_start_requires_both_independent_keys(monkeypatch):
    _keys(monkeypatch)
    client = TestClient(main.app)

    missing_execution = client.post(
        "/internal/vercel-prepare-acceptance/start",
        headers={"X-Preview-Acceptance-Key": "preview-key"},
    )
    assert missing_execution.status_code == 403

    wrong_preview = client.post(
        "/internal/vercel-prepare-acceptance/start",
        headers={
            "X-Preview-Acceptance-Key": "wrong",
            "X-Production-Execution-Key": "production-execution-key",
        },
    )
    assert wrong_preview.status_code == 403


def test_step4a_start_has_no_caller_target_surface(monkeypatch):
    _keys(monkeypatch)
    monkeypatch.setattr(
        main,
        "start_prepare_acceptance",
        lambda: {
            "acceptance_status": "READY_FOR_PROMOTION",
            "sacrificial_project_id": "prj_server_configured",
            "provider_write_performed": True,
            "production_traffic_changed": False,
        },
    )
    client = TestClient(main.app)
    response = client.post(
        "/internal/vercel-prepare-acceptance/start",
        headers={
            "X-Preview-Acceptance-Key": "preview-key",
            "X-Production-Execution-Key": "production-execution-key",
        },
        json={
            "target_project_id": "prj_attacker_supplied",
            "target_team_id": "team_attacker_supplied",
        },
    )
    assert response.status_code == 200
    assert response.json()["sacrificial_project_id"] == "prj_server_configured"


def test_step4a_reconcile_and_authorize_require_execution_key(monkeypatch):
    _keys(monkeypatch)
    client = TestClient(main.app)
    run_id = uuid4()

    reconcile = client.post(
        f"/internal/vercel-prepare-acceptance/{run_id}/reconcile",
        headers={"X-Preview-Acceptance-Key": "preview-key"},
    )
    assert reconcile.status_code == 403

    authorize = client.post(
        f"/internal/vercel-prepare-acceptance/{run_id}/authorize",
        headers={"X-Preview-Acceptance-Key": "preview-key"},
    )
    assert authorize.status_code == 403


def test_step4a_recover_requires_both_keys_and_is_read_only(monkeypatch):
    _keys(monkeypatch)
    run_id = uuid4()
    monkeypatch.setattr(
        main,
        "recover_prepare_acceptance",
        lambda value: {
            "id": str(value),
            "acceptance_status": "READY_FOR_PROMOTION",
            "provider_write_performed": True,
            "production_traffic_changed": False,
        },
    )
    client = TestClient(main.app)

    missing_execution = client.post(
        f"/internal/vercel-prepare-acceptance/{run_id}/recover",
        headers={"X-Preview-Acceptance-Key": "preview-key"},
    )
    assert missing_execution.status_code == 403

    recovered = client.post(
        f"/internal/vercel-prepare-acceptance/{run_id}/recover",
        headers={
            "X-Preview-Acceptance-Key": "preview-key",
            "X-Production-Execution-Key": "production-execution-key",
        },
    )
    assert recovered.status_code == 200
    payload = recovered.json()
    assert payload["provider_recovery_read_only"] is True
    assert payload["production_traffic_changed"] is False


def test_step4a_connector_safe_recovery_needs_no_secret_headers(monkeypatch):
    run_id = uuid4()
    monkeypatch.setattr(
        main,
        "recover_prepare_acceptance",
        lambda value: {
            "id": str(value),
            "acceptance_status": "READY_FOR_PROMOTION",
            "provider_write_performed": True,
            "prepare_write_count": 1,
            "production_traffic_changed": False,
        },
    )
    client = TestClient(main.app)

    recovered = client.get(
        f"/internal/vercel-prepare-acceptance/{run_id}/recover-readonly"
    )
    assert recovered.status_code == 200
    payload = recovered.json()
    assert payload["provider_recovery_read_only"] is True
    assert payload["provider_write_performed_by_recovery"] is False
    assert payload["production_traffic_changed"] is False
    assert payload["production_promotion_performed"] is False
    assert payload["production_rollback_performed"] is False
    assert recovered.headers["cache-control"] == "no-store, max-age=0"
    assert recovered.headers["x-robots-tag"] == "noindex"
