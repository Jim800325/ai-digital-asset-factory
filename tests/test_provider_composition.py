from uuid import UUID

from app.classifier import classify
from app.provider_composition import (
    COMPOSITION_TEMPLATES,
    composition_key,
    evaluate_composition,
    license_compatibility,
    plan_compositions,
)


def _provider(
    index: int,
    repo: str,
    role: str,
    *,
    score: float = 88.0,
    license_policy: str = "PERMISSIVE",
    commercial_fit: str = "COMMERCIAL_ALLOWED",
):
    return {
        "id": UUID(int=index),
        "repo_full_name": repo,
        "repo_url": f"https://github.com/{repo}",
        "provider_role": role,
        "license_policy": license_policy,
        "commercial_fit": commercial_fit,
        "commercial_model": "self-hosted / API / SaaS",
        "score": score,
        "stars": 10000 + index,
        "readiness": "BUILD_READY",
        "active": True,
        "queue_status": "QUEUED",
    }


def _provider_set():
    return [
        _provider(1, "example/crawler", "DATA_COLLECTION", score=91),
        _provider(2, "example/browser", "BROWSER_AUTOMATION", score=88),
        _provider(3, "example/agent", "AGENT_WORKFLOW", score=90),
        _provider(4, "example/ai-app", "AI_APP_PLATFORM", score=86),
        _provider(5, "example/automation", "AUTOMATION", score=94),
        _provider(6, "example/monitor", "MONITORING", score=89),
        _provider(
            7,
            "example/newsletter",
            "NEWSLETTER",
            score=84,
            license_policy="COPYLEFT",
            commercial_fit="AGPL_COMPLIANCE_REQUIRED",
        ),
        _provider(8, "example/distribution", "DISTRIBUTION", score=87),
    ]


def test_composition_templates_have_unique_ids():
    ids = [item["template_id"] for item in COMPOSITION_TEMPLATES]
    assert len(ids) == len(set(ids))
    assert {
        "MARKET_INTELLIGENCE_AUTOMATION",
        "AGENTIC_RESEARCH_FACTORY",
        "MONITORING_ALERTS_SERVICE",
    }.issubset(set(ids))


def test_composition_key_is_order_independent_by_member_list():
    members = [
        {**_provider(1, "example/crawler", "DATA_COLLECTION"), "slot": "collection"},
        {**_provider(3, "example/agent", "AGENT_WORKFLOW"), "slot": "intelligence"},
        {**_provider(5, "example/automation", "AUTOMATION"), "slot": "automation"},
    ]
    first = composition_key("X", members)
    second = composition_key("X", list(reversed(members)))
    assert first == second
    assert len(first) == 64


def test_license_compatibility_requires_review_for_copyleft_or_conditional():
    members = [
        {**_provider(1, "example/a", "DATA_COLLECTION"), "slot": "collection"},
        {
            **_provider(
                2,
                "example/b",
                "AUTOMATION",
                license_policy="COPYLEFT",
                commercial_fit="AGPL_COMPLIANCE_REQUIRED",
            ),
            "slot": "automation",
        },
    ]
    compatibility, constraints = license_compatibility(members)
    assert compatibility == "REVIEW"
    assert any("AGPL_COMPLIANCE_REQUIRED" in value for value in constraints)


def test_unknown_license_blocks_composition():
    members = [
        {**_provider(1, "example/a", "DATA_COLLECTION"), "slot": "collection"},
        {
            **_provider(
                2,
                "example/b",
                "AUTOMATION",
                license_policy="UNKNOWN",
                commercial_fit="LICENSE_REVIEW_REQUIRED",
            ),
            "slot": "automation",
        },
    ]
    compatibility, _ = license_compatibility(members)
    assert compatibility == "BLOCKED"


def test_plan_compositions_generates_deterministic_active_stacks():
    providers = _provider_set()
    first = plan_compositions(providers, candidates_per_slot=2, max_results=20)
    second = plan_compositions(list(reversed(providers)), candidates_per_slot=2, max_results=20)

    assert first
    assert [item["composition_key"] for item in first] == [
        item["composition_key"] for item in second
    ]
    assert any(item["composition_status"] == "ACTIVE" for item in first)
    for item in first:
        required_slots = {slot for slot, _ in next(
            template
            for template in COMPOSITION_TEMPLATES
            if template["template_id"] == item["template_id"]
        )["required_slots"]}
        assert required_slots.issubset({member["slot"] for member in item["members"]})
        assert len({str(member["id"]) for member in item["members"]}) == len(item["members"])
        assert 0 <= item["stack_score"] <= 100
        assert len(item["evidence_payload_sha256"]) == 64


def test_active_composition_text_is_classifiable_as_digital_asset():
    template = next(
        item
        for item in COMPOSITION_TEMPLATES
        if item["template_id"] == "MARKET_INTELLIGENCE_AUTOMATION"
    )
    members = [
        {**_provider(1, "example/crawler", "DATA_COLLECTION", score=92), "slot": "collection"},
        {**_provider(2, "example/agent", "AGENT_WORKFLOW", score=91), "slot": "intelligence"},
        {**_provider(3, "example/automation", "AUTOMATION", score=93), "slot": "automation"},
    ]
    result = evaluate_composition(template, members)
    kind, hits, pain = classify(
        result["opportunity_title"],
        result["hypothesis"],
    )
    assert result["composition_status"] == "ACTIVE"
    assert kind in {"DATASET_API", "INTELLIGENCE_REPORT", "MICRO_SAAS_TOOL", "TEMPLATE_WORKFLOW"}
    assert hits >= 1
    assert pain >= 1
