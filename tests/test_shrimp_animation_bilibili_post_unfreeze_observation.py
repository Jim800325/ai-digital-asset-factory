from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.providers.animation.shrimp.bilibili_post_unfreeze_observation import (
    _stage_target,
)
from app.providers.animation.shrimp.bilibili_reliability_governance import (
    _recommend,
)


def test_post_unfreeze_observation_ui_and_read_api():
    client=TestClient(app)
    page=client.get("/animation/reliability-review")
    assert page.status_code==200
    assert "POST-UNFREEZE OBSERVATION" in page.text
    assert "Gradual Exposure Ramp" in page.text
    assert "RESTORE ACCEPTANCE" in page.text

    response=client.get(
        "/v1/shrimp-animation/bilibili-post-unfreeze-observation"
    )
    assert response.status_code==200
    body=response.json()
    assert body["ramp"]==[25,50,75,100]
    assert body["auto_refreeze_execution"] is False
    assert body["human_governance_required_for_refreeze"] is True
    assert body["restore_acceptance_required"] is True
    assert body["provider_writes"] is False
    assert body["secrets_redacted"] is True


def test_restore_acceptance_gate_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_restore_acceptance_key",
        "ci-restore-acceptance-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "ci-monitor-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-post-unfreeze-observation/"
        "sessions/00000000-0000-0000-0000-000000000001/accept",
        headers={
            "X-Shrimp-Bilibili-Restore-Acceptance-Key":"wrong",
        },
        json={"actor":"ci-acceptance"},
    )
    assert response.status_code==403


def test_restore_acceptance_key_must_be_independent(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_restore_acceptance_key",
        "same-key",
    )
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "same-key",
    )
    response=TestClient(app).post(
        "/v1/shrimp-animation/bilibili-post-unfreeze-observation/"
        "sessions/00000000-0000-0000-0000-000000000001/accept",
        headers={
            "X-Shrimp-Bilibili-Restore-Acceptance-Key":"same-key",
        },
        json={"actor":"ci-acceptance"},
    )
    assert response.status_code==503


def test_observation_evaluator_is_protected(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "ci-observation-monitor-key",
    )
    monkeypatch.setattr(settings,"cron_secret","")
    response=TestClient(app).post(
        "/internal/shrimp-animation/bilibili-post-unfreeze-observation-evaluate",
        headers={"X-Shrimp-Health-Monitor-Key":"wrong"},
    )
    assert response.status_code==403


def test_gradual_ramp_mapping_is_deterministic():
    assert _stage_target(1)==(2,50,"ADVANCE_TO_50")
    assert _stage_target(2)==(3,75,"ADVANCE_TO_75")
    assert _stage_target(3)==(4,100,"ADVANCE_TO_100")
    assert _stage_target(4)==(None,None,"READY_FOR_ACCEPTANCE")


def test_observation_refreeze_signal_escalates_to_human_freeze_recommendation():
    snapshot={
        "scorecard":{
            "reliability_score":96.0,
            "reliability_grade":"A",
        },
        "burn":{"burn_status":"HEALTHY"},
        "open_regressions":[],
        "recurrence_clusters":[],
        "policy_recommendations":[],
        "post_unfreeze_observation":{
            "refreeze_recommendation":"REFREEZE_RECOMMENDED",
            "refreeze_reason":"stage provider ambiguity",
        },
    }
    recommendation,reason=_recommend(snapshot)
    assert recommendation=="FREEZE_RECOMMENDED"
    assert "post-unfreeze observation" in reason
