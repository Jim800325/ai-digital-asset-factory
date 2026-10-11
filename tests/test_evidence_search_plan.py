from fastapi.testclient import TestClient

from app.evidence_search_plan import PLAN_VERSION, _search_seed
from app.main import app


def test_search_seed_is_deterministic_and_removes_generic_github_words():
    row={
        "title":"GitHub Issues · Project planning for developers · GitHub",
        "canonical_title":"github issues project planning developers github",
    }
    assert _search_seed(row)=="project planning developers"


def test_evidence_search_plan_contract_is_plan_only_for_real_opportunity():
    client=TestClient(app)
    listing=client.get(
        "/v1/opportunity-workspace?stage=RESEARCH&gate=BLOCKED&min_score=75&limit=1"
    )
    assert listing.status_code==200
    items=listing.json()["items"]
    if not items:
        return

    opportunity_id=items[0]["id"]
    response=client.get(f"/v1/evidence-search-plans/{opportunity_id}")
    assert response.status_code==200
    payload=response.json()

    assert payload["status"]=="ok"
    assert payload["contract"]=="EVIDENCE_SEARCH_PLAN"
    assert payload["version"]==PLAN_VERSION
    assert payload["mode"]=="PLAN_ONLY"
    assert payload["execution_enabled"] is False
    assert payload["opportunity"]["id"]==opportunity_id
    assert payload["eligibility"]["recommended_for_expansion"] is True
    assert payload["objective"]["expansion_required"] is True
    assert payload["objective"]["target_status"]=="CANDIDATE"
    assert payload["tasks"]

    priorities=[task["priority"] for task in payload["tasks"]]
    assert priorities==sorted(priorities)

    existing_domains=set(payload["current_evidence"]["source_domains"])
    for task in payload["tasks"]:
        assert task["acceptance"]["must_be_new_domain"] is True
        assert task["acceptance"]["must_map_to_same_opportunity"] is True
        assert not existing_domains.intersection(task["preferred_domains"])
        assert task["queries"]

    assert payload["safety"]=={
        "network_execution":False,
        "database_write":False,
        "automatic_ingest":False,
        "automatic_candidate_promotion":False,
        "production_provider_writes":False,
    }


def test_evidence_search_plan_prioritizes_new_source_class():
    client=TestClient(app)
    listing=client.get(
        "/v1/opportunity-workspace?stage=RESEARCH&gate=BLOCKED&min_score=75&limit=20"
    ).json()

    for item in listing["items"]:
        detail=client.get(f"/v1/opportunity-workspace/{item['id']}").json()
        classes=set(detail["evidence"]["summary"]["source_classes"])
        if classes:
            plan=client.get(f"/v1/evidence-search-plans/{item['id']}").json()
            first=plan["tasks"][0]
            assert first["source_class"] not in classes
            return


def test_evidence_search_plan_exposes_exact_current_gate_thresholds():
    client=TestClient(app)
    listing=client.get("/v1/opportunity-workspace?limit=1").json()
    if not listing["items"]:
        return

    payload=client.get(
        f"/v1/evidence-search-plans/{listing['items'][0]['id']}"
    ).json()
    required={
        item["code"]:item["required"]
        for item in payload["gate"]["requirements"]
    }
    assert required=={
        "INDEPENDENT_SOURCES":2,
        "EVIDENCE_QUALITY":65.0,
        "SOURCE_DIVERSITY":60.0,
        "SIGNAL_STRENGTH":55.0,
        "CANDIDATE_SCORE":75.0,
    }
    assert payload["completion_criteria"]["requires_reaggregation"] is True


def test_evidence_search_plan_404_for_unknown_or_test_only_opportunity():
    client=TestClient(app)
    response=client.get(
        "/v1/evidence-search-plans/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code==404
