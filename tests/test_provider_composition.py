from uuid import UUID

from app.classifier import classify
from sqlalchemy import text

from app.db import engine
from app.provider_composition import (
    COMPOSITION_TEMPLATES,
    composition_discovery_items,
    composition_key,
    evaluate_composition,
    license_compatibility,
    plan_compositions,
    run_provider_composition_cycle,
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


def test_composition_cycle_persists_emits_and_stales_reversibly():
    repos = [
        ("planner-test/crawler", "DATA_COLLECTION", 93.0),
        ("planner-test/agent", "AGENT_WORKFLOW", 92.0),
        ("planner-test/automation", "AUTOMATION", 95.0),
        ("planner-test/distribution", "DISTRIBUTION", 90.0),
    ]
    run_ids = []
    try:
        with engine.begin() as db:
            for repo, role, score in repos:
                provider_id = db.execute(
                    text("""
                      INSERT INTO side_business_providers(
                        repo_full_name,repo_url,name,provider_role,discovery_origin,
                        license_policy,commercial_model,commercial_fit,
                        stars,score,readiness,active,last_scored_at)
                      VALUES(
                        :repo,:url,:name,:role,'DISCOVERED',
                        'PERMISSIVE','self-hosted / API / SaaS','COMMERCIAL_ALLOWED',
                        10000,:score,'BUILD_READY',true,now())
                      RETURNING id
                    """),
                    {
                        "repo": repo,
                        "url": f"https://github.com/{repo}",
                        "name": repo.rsplit("/", 1)[-1],
                        "role": role,
                        "score": score,
                    },
                ).scalar_one()
                db.execute(
                    text("""
                      INSERT INTO side_business_build_queue(
                        provider_id,queue_status,provider_score,reason)
                      VALUES(:provider_id,'QUEUED',:score,'planner integration acceptance')
                    """),
                    {"provider_id": provider_id, "score": score},
                )

        first = run_provider_composition_cycle()
        run_ids.append(first["run_id"])
        assert first["status"] == "SUCCESS"
        assert first["compositions_generated"] >= 2
        assert first["active_compositions"] >= 2
        assert first["hypotheses_emitted"] == first["active_compositions"]

        emitted = composition_discovery_items(limit=20)
        planner_items = [
            item
            for item in emitted
            if item["source_type"] == "GITHUB_SIDE_BUSINESS_COMPOSITION"
            and "planner-test/" in item["text"]
        ]
        assert planner_items
        assert all(len(item["fingerprint"]) == 64 for item in planner_items)
        assert all("independent market evidence" in item["text"] for item in planner_items)

        with engine.connect() as db:
            active = db.execute(
                text("""
                  SELECT id,composition_status,stack_score,provider_snapshot
                  FROM side_business_compositions
                  WHERE provider_snapshot::text LIKE '%planner-test/%'
                  ORDER BY stack_score DESC
                """)
            ).mappings().all()
            assert active
            assert all(row["composition_status"] == "ACTIVE" for row in active)
            composition_ids = [row["id"] for row in active]

            member_count = db.execute(
                text("""
                  SELECT COUNT(*)
                  FROM side_business_composition_members
                  WHERE composition_id=ANY(:ids)
                """),
                {"ids": composition_ids},
            ).scalar_one()
            assert member_count >= len(active) * 3

        with engine.begin() as db:
            automation_id = db.execute(
                text("""
                  UPDATE side_business_providers
                  SET readiness='WATCH',score=60,updated_at=now()
                  WHERE repo_full_name='planner-test/automation'
                  RETURNING id
                """)
            ).scalar_one()
            db.execute(
                text("""
                  UPDATE side_business_build_queue
                  SET queue_status='STALE',provider_score=60,updated_at=now()
                  WHERE provider_id=:provider_id
                """),
                {"provider_id": automation_id},
            )

        second = run_provider_composition_cycle()
        run_ids.append(second["run_id"])
        assert second["status"] == "SUCCESS"
        assert second["compositions_generated"] == 0
        assert second["active_compositions"] == 0
        assert second["stale_compositions"] >= len(active)

        emitted_after = composition_discovery_items(limit=20)
        assert not any("planner-test/" in item["text"] for item in emitted_after)

        with engine.connect() as db:
            statuses = db.execute(
                text("""
                  SELECT DISTINCT composition_status
                  FROM side_business_compositions
                  WHERE provider_snapshot::text LIKE '%planner-test/%'
                """)
            ).scalars().all()
            assert statuses == ["STALE"]
    finally:
        with engine.begin() as db:
            db.execute(
                text("""
                  DELETE FROM side_business_compositions
                  WHERE provider_snapshot::text LIKE '%planner-test/%'
                """)
            )
            db.execute(
                text("""
                  DELETE FROM side_business_providers
                  WHERE repo_full_name LIKE 'planner-test/%'
                """)
            )
            if run_ids:
                db.execute(
                    text("""
                      DELETE FROM side_business_composition_runs
                      WHERE id::text=ANY(:ids)
                    """),
                    {"ids": run_ids},
                )
