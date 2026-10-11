from uuid import UUID

from fastapi.testclient import TestClient

from app.main import app
from app.opportunity_workspace import (
    EVIDENCE_GATE_THRESHOLDS,
    evidence_gate_checks,
)


def test_opportunity_workspace_list_contract_is_read_only_and_filters_test_data():
    client = TestClient(app)
    response = client.get("/v1/opportunity-workspace?limit=20")
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "ok"
    assert set(payload) == {
        "status",
        "filters",
        "pagination",
        "facets",
        "items",
    }
    assert payload["pagination"]["limit"] == 20
    assert set(payload["facets"]) == {"stages", "asset_types", "gate"}

    for item in payload["items"]:
        assert not item["title"].startswith("[TEST_ONLY]")
        assert set(item) == {
            "id",
            "title",
            "canonical_title",
            "asset_type",
            "score",
            "stage",
            "independent_source_count",
            "evidence_count",
            "evidence_quality_score",
            "source_diversity_score",
            "signal_strength_score",
            "evidence_gate_passed",
            "research_validation_score",
            "build_readiness",
            "build_proposal_status",
            "gate_failed_checks",
            "next_action",
            "updated_at",
        }
        assert set(item["next_action"]) == {"code", "label", "reason", "target"}


def test_opportunity_workspace_supports_business_filters_and_sorting():
    client = TestClient(app)
    response = client.get(
        "/v1/opportunity-workspace"
        "?stage=RESEARCH&gate=BLOCKED&min_score=60&min_sources=0"
        "&sort=evidence_quality&order=desc&limit=10"
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["filters"]["stage"] == "RESEARCH"
    assert payload["filters"]["gate"] == "BLOCKED"
    assert payload["filters"]["min_score"] == 60.0
    assert payload["filters"]["sort"] == "evidence_quality"
    for item in payload["items"]:
        assert item["stage"] == "RESEARCH"
        assert item["evidence_gate_passed"] is False
        assert item["score"] >= 60


def test_opportunity_workspace_rejects_unknown_filters():
    client = TestClient(app)
    response = client.get("/v1/opportunity-workspace?stage=PROFITABLE")
    assert response.status_code == 422

    response = client.get("/v1/opportunity-workspace?sort=DROP_TABLE")
    assert response.status_code == 422


def test_evidence_gate_checks_match_current_business_thresholds():
    row = {
        "independent_source_count": 1,
        "evidence_quality_score": 80.2,
        "source_diversity_score": 45,
        "signal_strength_score": 100,
        "score": 80.85,
    }
    checks = {item["code"]: item for item in evidence_gate_checks(row)}

    assert EVIDENCE_GATE_THRESHOLDS == {
        "independent_sources": 2,
        "evidence_quality": 65.0,
        "source_diversity": 60.0,
        "signal_strength": 55.0,
        "candidate_score": 75.0,
    }
    assert checks["INDEPENDENT_SOURCES"]["passed"] is False
    assert checks["EVIDENCE_QUALITY"]["passed"] is True
    assert checks["SOURCE_DIVERSITY"]["passed"] is False
    assert checks["SIGNAL_STRENGTH"]["passed"] is True
    assert checks["CANDIDATE_SCORE"]["passed"] is True


def test_opportunity_workspace_detail_contract_when_real_item_exists():
    client = TestClient(app)
    listing = client.get("/v1/opportunity-workspace?limit=1").json()
    if not listing["items"]:
        return

    opportunity_id = listing["items"][0]["id"]
    UUID(opportunity_id)
    response = client.get(f"/v1/opportunity-workspace/{opportunity_id}")
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "ok"
    assert set(payload) == {
        "status",
        "opportunity",
        "scorecard",
        "why_worth_attention",
        "gate",
        "evidence",
        "aliases",
        "research",
        "validation",
        "build_proposal",
        "lifecycle",
        "next_action",
    }
    assert payload["opportunity"]["id"] == opportunity_id
    assert not payload["opportunity"]["title"].startswith("[TEST_ONLY]")
    assert set(payload["gate"]) == {"passed", "checks", "failed_checks"}
    assert set(payload["next_action"]) == {"code", "label", "reason", "target"}
    assert payload["lifecycle"][0] == {"stage": "DISCOVERED", "status": "DONE"}


def test_opportunity_workspace_detail_404_for_unknown_id():
    client = TestClient(app)
    response = client.get(
        "/v1/opportunity-workspace/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 404
