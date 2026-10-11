from fastapi.testclient import TestClient

from app.main import app
import app.workbench_summary as workbench
from app.workbench_actions import build_workbench_actions


def test_workbench_summary_contract_is_business_first_and_read_only():
    response = TestClient(app).get("/v1/workbench-summary")
    assert response.status_code == 200
    payload = response.json()

    assert payload["product"] == "SIDE_BUSINESS_WORKBENCH"
    assert payload["mode"] == "OBSERVE"
    assert set(payload["funnel"]) == {
        "discovered",
        "watch",
        "research",
        "candidate",
        "validating",
        "build_ready",
        "building",
        "published",
        "profitable",
    }
    assert payload["stage_availability"]["published"] == "PLANNED"
    assert payload["stage_availability"]["profitable"] == "PLANNED"

    assert set(payload["system"]) == {
        "status",
        "database",
        "migrations",
        "pipeline",
        "worker_count",
    }

    for opportunity in payload["top_opportunities"]:
        assert not opportunity["title"].startswith("[TEST_ONLY]")
        assert set(opportunity) == {
            "id",
            "title",
            "asset_type",
            "score",
            "stage",
            "independent_source_count",
            "evidence_count",
            "evidence_quality_score",
            "source_diversity_score",
            "signal_strength_score",
            "evidence_gate_passed",
            "build_readiness",
            "next_action",
            "updated_at",
        }

    serialized = response.text.lower()
    forbidden = (
        "database_url",
        "redis_url",
        "execution_key",
        "github_token",
        "source_commit",
        "vercel_deployment_id",
        "source_tree_sha256",
        "prompt_tokens",
        "completion_tokens",
        "estimated_cost_usd",
    )
    for key in forbidden:
        assert key not in serialized


def test_workbench_actions_prioritize_evidence_gate():
    actions = build_workbench_actions(
        {
            "evidence_blocked": 91,
            "candidate": 0,
            "candidate_reports_missing": 0,
            "candidate_validations_missing": 0,
            "build_approval_pending": 0,
            "release_review_pending": 0,
        }
    )

    assert actions[0]["type"] == "EVIDENCE_EXPANSION"
    assert actions[0]["priority"] == "HIGH"
    assert actions[0]["count"] == 91
    assert actions[0]["href"] == "/opportunities?gate=blocked"


def test_workbench_actions_surface_human_approvals():
    actions = build_workbench_actions(
        {
            "evidence_blocked": 0,
            "candidate": 1,
            "candidate_reports_missing": 0,
            "candidate_validations_missing": 0,
            "build_approval_pending": 2,
            "release_review_pending": 1,
        }
    )

    types = [item["type"] for item in actions]
    assert "BUILD_APPROVAL" in types
    assert "RELEASE_REVIEW" in types


def test_disabled_manual_pipeline_does_not_degrade_platform_health(monkeypatch):
    monkeypatch.setattr(workbench.settings, "manual_pipeline_execution_enabled", False)

    def should_not_probe():
        raise AssertionError("manual pipeline readiness should not be probed when disabled")

    monkeypatch.setattr(workbench, "manual_pipeline_readiness", should_not_probe)

    payload = workbench.build_workbench_summary()
    assert payload["system"]["pipeline"] == "DISABLED"
    assert payload["system"]["status"] == "READY"
