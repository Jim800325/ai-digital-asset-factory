import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.build_proposals import decide_build_proposal, ensure_build_proposal
from app.db import engine
from app.main import app
from app.production_provider_contract import get_provider_job
from app.providers.animation.asset_registry import (
    list_animation_registry,
    register_background,
    register_character,
    register_reusable_asset,
    register_voice_profile,
)
from app.providers.animation.models import CharacterBrief, ContentBrief
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.provider import (
    create_shrimp_animation_job,
    run_deterministic_planning,
    update_shrimp_content_brief,
)
from app.providers.animation.shrimp.resource_planning import (
    list_resource_plans,
    run_resource_planning,
)


PREFIX = "shrimp-step2"


def _brief(premise: str = (
    "A young inspector and a cautious adviser discover a shipping error "
    "and must solve it before the market opens."
)) -> ContentBrief:
    return ContentBrief(
        episode_id="shrimp-step2-acceptance",
        title="港口资源计划",
        premise=premise,
        audience="short-form story animation viewers",
        language="zh-CN",
        target_duration_ms=45000,
        scene_count=3,
        characters=[
            CharacterBrief(
                character_id="step2_hero",
                display_name="阿海",
                role="young inspector",
                voice_profile_id="step2_hero_voice",
            ),
            CharacterBrief(
                character_id="step2_advisor",
                display_name="老周",
                role="cautious adviser",
                voice_profile_id="step2_advisor_voice",
            ),
        ],
        background_hints=[
            "step2_harbor_day",
            "step2_warehouse",
            "step2_office",
        ],
        style_tags=["2d", "story", "comedy"],
    )


def _create_content_ip_proposal():
    fingerprint = "shrimp-step2-opportunity-fixture"
    with engine.begin() as db:
        opportunity_id = db.execute(
            text("""
              INSERT INTO digital_asset_opportunities(
                fingerprint,title,asset_type,problem,target_customer,
                monetization_model,source_url,score,status,
                research_validation_score,build_readiness)
              VALUES(
                :fingerprint,'港口资源计划动画内容包','CONTENT_IP',
                'manual story production is slow',
                'short-form animation viewers',
                'content licensing / channel package',
                'https://shrimp-step2.test/opportunity',
                92,'CANDIDATE',100,'BUILD_READY')
              RETURNING id
            """),
            {"fingerprint": fingerprint},
        ).scalar_one()

        db.execute(
            text("""
              INSERT INTO research_reports(
                opportunity_id,report_status,problem,buyer,
                existing_alternatives,evidence,monetization,
                build_complexity,risks,why_now,generator_version,observe_only)
              VALUES(
                :id,'GENERATED',
                'manual story animation production is slow',
                'short-form animation creators',
                'manual editing and one-off animation tools',
                'two independent acceptance sources',
                'content licensing / channel package',
                'medium',
                'asset provenance and voice rights',
                'reusable deterministic production reduces repeated work',
                'research-v0.2-deterministic',true)
            """),
            {"id": opportunity_id},
        )
        db.execute(
            text("""
              INSERT INTO research_validations(
                opportunity_id,validation_status,buyer_status,
                competitors_status,pricing_status,willingness_to_pay_status,
                market_gap_status,completeness_score,validation_gate_passed,
                build_readiness,validator_version,observe_only)
              VALUES(
                :id,'CURRENT','VALIDATED','VALIDATED','VALIDATED',
                'VALIDATED','VALIDATED',100,true,'BUILD_READY',
                'validation-v0.2-deterministic',true)
            """),
            {"id": opportunity_id},
        )

    proposal = ensure_build_proposal(opportunity_id)
    assert proposal is not None
    decided = decide_build_proposal(
        proposal["proposal_id"],
        decision="APPROVE",
        reason="shrimp Step 2 acceptance",
        actor="ci-human",
    )
    assert decided["proposal_status"] == "APPROVED"
    return opportunity_id, proposal["proposal_id"]


def _seed_registry():
    register_voice_profile(
        voice_profile_id="step2_hero_voice",
        provenance="CI synthetic voice fixture",
        usage_rights="APPROVED",
        adapter_hint="gpt-sovits-local",
        source_type="SYNTHETIC",
    )
    register_voice_profile(
        voice_profile_id="step2_advisor_voice",
        provenance="CI licensed voice fixture pending review",
        usage_rights="REVIEW_REQUIRED",
        adapter_hint="gpt-sovits-local",
        source_type="LICENSED",
    )

    register_reusable_asset(
        asset_key="step2_hero_base",
        asset_kind="CHARACTER_BASE",
        storage_uri="fixture://step2/hero.png",
        sha256="1" * 64,
        license_id="CI-TEST-LICENSE",
        provenance="CI generated fixture",
        usage_rights="APPROVED",
    )
    register_reusable_asset(
        asset_key="step2_advisor_base",
        asset_kind="CHARACTER_BASE",
        storage_uri="fixture://step2/advisor.png",
        sha256="2" * 64,
        license_id="CI-TEST-LICENSE",
        provenance="CI generated fixture",
        usage_rights="APPROVED",
    )
    register_reusable_asset(
        asset_key="step2_harbor_asset",
        asset_kind="BACKGROUND",
        storage_uri="fixture://step2/harbor.png",
        sha256="3" * 64,
        license_id="CI-TEST-LICENSE",
        provenance="CI generated fixture",
        usage_rights="APPROVED",
    )
    register_reusable_asset(
        asset_key="step2_office_asset",
        asset_kind="BACKGROUND",
        storage_uri="fixture://step2/office.png",
        sha256="4" * 64,
        license_id="CI-TEST-LICENSE",
        provenance="CI generated fixture",
        usage_rights="APPROVED",
    )

    register_character(
        character_id="step2_hero",
        display_name="阿海",
        base_asset_key="step2_hero_base",
        anchor_points={"feet": [0.5, 1.0]},
        voice_profile_id="step2_hero_voice",
    )
    register_character(
        character_id="step2_advisor",
        display_name="老周",
        base_asset_key="step2_advisor_base",
        anchor_points={"feet": [0.5, 1.0]},
        voice_profile_id="step2_advisor_voice",
    )
    register_background(
        background_id="step2_harbor_day",
        asset_key="step2_harbor_asset",
    )
    register_background(
        background_id="step2_office",
        asset_key="step2_office_asset",
    )


def _cleanup_registry():
    with engine.begin() as db:
        db.execute(
            text("""
              DELETE FROM animation_character_registry
              WHERE character_id LIKE 'step2_%'
            """)
        )
        db.execute(
            text("""
              DELETE FROM animation_background_registry
              WHERE background_id LIKE 'step2_%'
            """)
        )
        db.execute(
            text("""
              DELETE FROM animation_reusable_assets
              WHERE asset_key LIKE 'step2_%'
            """)
        )
        db.execute(
            text("""
              DELETE FROM animation_voice_profiles
              WHERE voice_profile_id LIKE 'step2_%'
            """)
        )


def test_step2_registry_contains_deterministic_action_and_camera_primitives():
    registry = list_animation_registry()
    actions = {row["action_key"] for row in registry["actions"]}
    cameras = {row["camera_key"] for row in registry["cameras"]}
    assert {
        "idle", "talk", "enter", "exit", "nod", "laugh",
    }.issubset(actions)
    assert {
        "static", "pan", "zoom", "push_in", "pull_out",
        "focus_left", "focus_right",
    }.issubset(cameras)


def test_step2_resource_plans_are_versioned_reversible_and_do_not_execute_assets():
    opportunity_id = None
    try:
        register_shrimp_animation_provider()
        _seed_registry()
        opportunity_id, proposal_id = _create_content_ip_proposal()
        brief = _brief()

        created = create_shrimp_animation_job(
            proposal_id,
            brief,
            requested_by="ci-shrimp-step2",
        )
        job_id = created["job_id"]
        run_deterministic_planning(job_id)

        first = run_resource_planning(job_id)
        assert first["assets_stage_executed"] is False
        assert first["voices_stage_executed"] is False
        assert first["external_side_effects"] == "DENY"

        asset = first["asset_plan"]
        assert asset["plan_version"] == 1
        assert asset["reuse_ready_count"] == 4
        assert asset["generation_required_count"] == 1
        assert asset["review_required_count"] == 0
        assert asset["blocked_count"] == 0
        assert asset["ready_for_asset_execution"] is True

        voice = first["voice_plan"]
        assert voice["plan_version"] == 1
        assert voice["ready_count"] == 3
        assert voice["review_required_count"] == 3
        assert voice["profile_required_count"] == 0
        assert voice["adapter_required_count"] == 0
        assert voice["blocked_count"] == 0
        assert voice["ready_for_synthesis"] is False

        generic = get_provider_job(job_id)
        statuses = {
            row["stage_key"]: row["stage_status"]
            for row in generic["stages"]
        }
        assert statuses["ASSETS"] == "PENDING"
        assert statuses["VOICES"] == "PENDING"
        assert generic["production_execution_enabled"] is False
        assert generic["publish_enabled"] is False

        current = list_resource_plans(job_id)
        assert {(row["plan_kind"], row["plan_version"]) for row in current} == {
            ("ASSET", 1),
            ("VOICE", 1),
        }

        repeated = run_resource_planning(job_id)
        assert repeated["asset_plan"]["changed"] is False
        assert repeated["voice_plan"]["changed"] is False
        assert repeated["asset_plan"]["plan_version"] == 1
        assert repeated["voice_plan"]["plan_version"] == 1

        register_reusable_asset(
            asset_key="step2_warehouse_asset",
            asset_kind="BACKGROUND",
            storage_uri="fixture://step2/warehouse.png",
            sha256="5" * 64,
            license_id="CI-TEST-LICENSE",
            provenance="CI generated fixture",
            usage_rights="APPROVED",
        )
        register_background(
            background_id="step2_warehouse",
            asset_key="step2_warehouse_asset",
        )
        with_warehouse = run_resource_planning(job_id)
        assert with_warehouse["asset_plan"]["changed"] is True
        assert with_warehouse["asset_plan"]["plan_version"] == 2
        assert with_warehouse["asset_plan"]["reuse_ready_count"] == 5
        assert with_warehouse["asset_plan"]["generation_required_count"] == 0
        assert with_warehouse["voice_plan"]["changed"] is False
        assert with_warehouse["voice_plan"]["plan_version"] == 1

        register_voice_profile(
            voice_profile_id="step2_advisor_voice",
            provenance="CI licensed voice fixture approved for test use",
            usage_rights="APPROVED",
            adapter_hint="gpt-sovits-local",
            source_type="LICENSED",
        )
        approved_voice = run_resource_planning(job_id)
        assert approved_voice["asset_plan"]["changed"] is False
        assert approved_voice["asset_plan"]["plan_version"] == 2
        assert approved_voice["voice_plan"]["changed"] is True
        assert approved_voice["voice_plan"]["plan_version"] == 2
        assert approved_voice["voice_plan"]["ready_count"] == 6
        assert approved_voice["voice_plan"]["review_required_count"] == 0
        assert approved_voice["voice_plan"]["ready_for_synthesis"] is True

        with pytest.raises(Exception):
            with engine.begin() as db:
                db.execute(
                    text("""
                      UPDATE shrimp_animation_resource_plans
                      SET content=CAST('{"tampered":true}' AS jsonb)
                      WHERE id=(
                        SELECT id
                        FROM shrimp_animation_resource_plans
                        WHERE provider_job_id=CAST(:job_id AS uuid)
                          AND plan_status='CURRENT'
                        LIMIT 1
                      )
                    """),
                    {"job_id": job_id},
                )

        changed_brief = _brief(
            "A revised harbor incident sends the inspector into the warehouse, "
            "where a missing customs manifest changes both the story and dialogue context."
        )
        update_shrimp_content_brief(
            job_id,
            changed_brief,
            actor="ci-shrimp-step2",
        )
        assert list_resource_plans(job_id) == []

        run_deterministic_planning(job_id)
        rebuilt = run_resource_planning(job_id)
        assert rebuilt["asset_plan"]["plan_version"] == 3
        assert rebuilt["voice_plan"]["plan_version"] == 3

        after = get_provider_job(job_id)
        after_status = {
            row["stage_key"]: row["stage_status"]
            for row in after["stages"]
        }
        assert after_status["SCENE"] == "SUCCEEDED"
        assert after_status["ASSETS"] == "STALE"
        assert after_status["VOICES"] == "STALE"
        assert after["production_execution_enabled"] is False
        assert after["publish_enabled"] is False

        history = list_resource_plans(job_id, include_stale=True)
        asset_versions = [
            row["plan_version"]
            for row in history
            if row["plan_kind"] == "ASSET"
        ]
        voice_versions = [
            row["plan_version"]
            for row in history
            if row["plan_kind"] == "VOICE"
        ]
        assert asset_versions == [3, 2, 1]
        assert voice_versions == [3, 2, 1]
        assert sum(row["plan_status"] == "CURRENT" for row in history) == 2

        client = TestClient(app)
        registry_response = client.get("/v1/animation/registry")
        assert registry_response.status_code == 200
        plan_response = client.get(
            f"/v1/shrimp-animation/jobs/{job_id}/resource-plans"
        )
        assert plan_response.status_code == 200
        assert len(plan_response.json()) == 2
    finally:
        if opportunity_id is not None:
            with engine.begin() as db:
                db.execute(
                    text("""
                      DELETE FROM digital_asset_opportunities
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": str(opportunity_id)},
                )
        _cleanup_registry()
