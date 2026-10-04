from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.providers.animation.shrimp.bilibili_post_restore_certification import (
    _reopen_policy,
    _trigger_codes,
)
from app.providers.animation.shrimp.bilibili_reliability_governance import (
    _recommend,
)


def test_post_restore_certification_ui_and_read_api():
    client=TestClient(app)
    page=client.get("/animation/reliability-review")
    assert page.status_code==200
    assert "POST-RESTORE CERTIFICATION" in page.text
    assert "STABILITY BASELINE" in page.text
    assert "REOPEN POLICY" in page.text

    response=client.get(
        "/v1/shrimp-animation/bilibili-post-restore-certification"
    )
    assert response.status_code==200
    body=response.json()
    assert body["baseline_immutable"] is True
    assert body["automatic_policy_change"] is False
    assert body["human_governance_required_for_reopen"] is True
    assert body["provider_writes"] is False
    assert body["secrets_redacted"] is True


def test_certification_cycle_endpoint_is_protected(monkeypatch):
    monkeypatch.setattr(
        settings,
        "shrimp_bilibili_health_monitor_key",
        "ci-cert-monitor-key",
    )
    monkeypatch.setattr(settings,"cron_secret","")
    response=TestClient(app).post(
        "/internal/shrimp-animation/bilibili-post-restore-certification-cycle",
        headers={"X-Shrimp-Health-Monitor-Key":"wrong"},
    )
    assert response.status_code==403


def test_reopen_policy_is_deterministic():
    baseline={"reliability_score":96.0}
    promoted={
        "reliability_score_min":85.0,
        "ack_success_target_percent":99.0,
        "recovery_success_target_percent":99.0,
        "ambiguity_max_percent":1.0,
    }
    policy=_reopen_policy(baseline,promoted)
    assert policy["reliability_score_floor"]==86.0
    assert policy["reliability_score_drop_points"]==10.0
    assert policy["ack_success_below_percent"]==99.0
    assert policy["recovery_success_below_percent"]==99.0
    assert policy["ambiguity_above_percent"]==1.0
    assert policy["automatic_policy_change"] is False
    assert policy["human_governance_required"] is True


def test_trigger_codes_detect_long_term_reliability_regression():
    cert={
        "stability_baseline":{"reliability_score":96.0},
        "reopen_policy":{
            "reliability_score_floor":86.0,
            "reliability_score_drop_points":10.0,
            "ack_success_below_percent":99.0,
            "recovery_success_below_percent":99.0,
            "ambiguity_above_percent":1.0,
            "burn_not_healthy":True,
            "open_regression_above":0,
            "recurring_root_cause_above":0,
            "nonclosed_circuit_above":0,
            "unresolved_incident_above":0,
        },
    }
    current={
        "scorecard_present":True,
        "reliability_score":84.0,
        "ack_success_rate":98.0,
        "recovery_success_rate":97.0,
        "ambiguity_rate_percent":2.5,
        "burn_status":"WATCH",
        "open_regression_count":1,
        "recurring_cluster_count":1,
        "nonclosed_circuit_count":1,
        "unresolved_incident_count":1,
    }
    triggers=_trigger_codes(cert,current)
    assert "RELIABILITY_SCORE_BELOW_FLOOR" in triggers
    assert "RELIABILITY_SCORE_REGRESSION" in triggers
    assert "ACK_SLO_BREACH" in triggers
    assert "RECOVERY_SLO_BREACH" in triggers
    assert "AMBIGUITY_SLO_BREACH" in triggers
    assert "ERROR_BUDGET_BURN" in triggers
    assert "OPEN_REGRESSION" in triggers
    assert "ROOT_CAUSE_RECURRENCE" in triggers
    assert "CIRCUIT_REOPENED" in triggers
    assert "INCIDENT_REOPENED" in triggers


def test_certification_reopen_signal_requires_human_governance():
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
        "post_restore_certification_reopen":{
            "event_id":"event-1",
            "trigger_codes":["AMBIGUITY_SLO_BREACH"],
        },
    }
    recommendation,reason=_recommend(snapshot)
    assert recommendation=="CAUTION"
    assert "certification reopened" in reason
