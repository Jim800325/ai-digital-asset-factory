from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    _certification_validity,
)
from app.providers.animation.shrimp.bilibili_reliability_governance import (
    _recommend,
)


def test_certification_renewal_ui_and_read_api():
    client=TestClient(app)
    page=client.get("/animation/reliability-review")
    assert page.status_code==200
    assert "CERTIFICATION VALIDITY" in page.text
    assert "BASELINE RENEWAL" in page.text
    assert "ATTESTATION HISTORY" in page.text

    response=client.get(
        "/v1/shrimp-animation/bilibili-certification-renewal"
    )
    assert response.status_code==200
    body=response.json()
    assert body["automatic_recertification"] is False
    assert body["human_recertification_gate_required"] is True
    assert body["provider_writes"] is False
    assert body["secrets_redacted"] is True


def test_certification_validity_window_is_deterministic(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_certification_valid_days",
        30,
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_certification_expiry_warning_days",
        7,
    )
    now=datetime(2026,10,5,12,0,0,tzinfo=timezone.utc)
    valid_from,renewal_due,expires_at=_certification_validity(now)
    assert valid_from==now
    assert (expires_at-valid_from).days==30
    assert (expires_at-renewal_due).days==7


def test_recertification_gate_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_recertification_key",
        "ci-recert-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_governance_key",
        "ci-governance-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-recertification-candidates/"
        "00000000-0000-0000-0000-000000000001/decision",
        headers={"X-Shrimp-Bilibili-Recertification-Key":"wrong"},
        json={
            "decision":"APPROVE",
            "reason":"CI governance re-certification",
            "actor":"ci-recertification",
            "candidate_sha256":"0"*64,
        },
    )
    assert response.status_code==403


def test_recertification_key_must_be_independent(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_recertification_key",
        "same-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_reliability_governance_key",
        "same-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-recertification-candidates/"
        "00000000-0000-0000-0000-000000000001/decision",
        headers={"X-Shrimp-Bilibili-Recertification-Key":"same-key"},
        json={
            "decision":"APPROVE",
            "reason":"CI key independence",
            "actor":"ci-recertification",
            "candidate_sha256":"0"*64,
        },
    )
    assert response.status_code==503


def test_certification_renewal_cycle_is_protected(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "ci-monitor-key",
    )
    monkeypatch.setattr(settings,"cron_secret","")
    response=TestClient(app).post(
        "/internal/shrimp-animation/bilibili-certification-renewal-cycle",
        headers={"X-Shrimp-Health-Monitor-Key":"wrong"},
    )
    assert response.status_code==403


def test_expired_certification_requires_human_governance_recertification():
    snapshot={
        "scorecard":{
            "reliability_score":96.0,
            "reliability_grade":"A",
        },
        "burn":{"burn_status":"HEALTHY"},
        "open_regressions":[],
        "recurrence_clusters":[],
        "policy_recommendations":[],
        "post_unfreeze_observation":None,
        "post_restore_certification_reopen":None,
        "certification_lifecycle":{
            "certification_status":"EXPIRED",
            "pending_recertification":True,
        },
    }
    recommendation,reason=_recommend(snapshot)
    assert recommendation=="CAUTION"
    assert "certification expired" in reason
