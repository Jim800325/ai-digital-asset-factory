import pytest
from sqlalchemy import text

from app.build_proposals import decide_build_proposal, ensure_build_proposal
from app.db import engine
from app.production_provider_contract import get_provider_job
from app.providers.animation.models import (
    CharacterBrief,
    ContentBrief,
    manifest_sha256,
)
from app.providers.animation.registry import (
    SHRIMP_ANIMATION_SPEC,
    register_shrimp_animation_provider,
)
from app.providers.animation.shrimp.provider import (
    create_shrimp_animation_job,
    get_shrimp_animation_job,
    run_deterministic_planning,
    update_shrimp_content_brief,
)
from app.providers.animation.shrimp.scene_planner import plan_scenes
from app.providers.animation.shrimp.script_planner import plan_script
from app.providers.animation.shrimp.story_planner import plan_story


def _brief(premise: str = (
    "A young inspector and a cautious adviser discover a shipping error "
    "and must solve it before the market opens."
)) -> ContentBrief:
    return ContentBrief(
        episode_id="shrimp-step1-acceptance",
        title="港口乌龙事件",
        premise=premise,
        audience="short-form story animation viewers",
        language="zh-CN",
        target_duration_ms=45000,
        scene_count=3,
        characters=[
            CharacterBrief(
                character_id="hero",
                display_name="阿海",
                role="young inspector",
                voice_profile_id="hero-neutral",
            ),
            CharacterBrief(
                character_id="advisor",
                display_name="老周",
                role="cautious adviser",
                voice_profile_id="advisor-neutral",
            ),
        ],
        background_hints=["harbor_day", "warehouse", "office"],
        style_tags=["2d", "story", "comedy"],
    )


def _create_content_ip_proposal():
    fingerprint = "shrimp-step1-opportunity-fixture"
    with engine.begin() as db:
        opportunity_id = db.execute(
            text("""
              INSERT INTO digital_asset_opportunities(
                fingerprint,title,asset_type,problem,target_customer,
                monetization_model,source_url,score,status,
                research_validation_score,build_readiness)
              VALUES(
                :fingerprint,'港口故事动画内容包','CONTENT_IP',
                'manual story production is slow',
                'short-form animation viewers',
                'content licensing / channel package',
                'https://shrimp-step1.test/opportunity',
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
                'asset provenance and render quality',
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
    assert proposal["proposal_status"] == "PENDING_APPROVAL"
    decided = decide_build_proposal(
        proposal["proposal_id"],
        decision="APPROVE",
        reason="shrimp Step 1 acceptance",
        actor="ci-human",
    )
    assert decided["proposal_status"] == "APPROVED"
    return opportunity_id, proposal["proposal_id"]


def test_shrimp_planners_are_deterministic_and_unicode_safe():
    brief = _brief()
    story_a = plan_story(brief)
    story_b = plan_story(brief)
    assert story_a == story_b
    assert story_a.title == "港口乌龙事件"
    assert story_a.brief_sha256 == manifest_sha256(brief)

    script_a = plan_script(brief, story_a)
    script_b = plan_script(brief, story_b)
    assert script_a == script_b
    assert script_a.story_sha256 == manifest_sha256(story_a)

    scene_a = plan_scenes(brief, script_a)
    scene_b = plan_scenes(brief, script_b)
    assert scene_a == scene_b
    assert scene_a.script_sha256 == manifest_sha256(script_a)
    assert sum(scene.duration_ms for scene in scene_a.scenes) == brief.target_duration_ms
    assert all(
        0 <= action.x <= 1 and 0 <= action.y <= 1
        for scene in scene_a.scenes
        for action in scene.characters
    )


def test_script_rejects_story_from_another_brief():
    brief = _brief()
    changed = _brief(
        "A different premise changes the story source and must invalidate "
        "the downstream deterministic script."
    )
    story = plan_story(brief)
    with pytest.raises(ValueError, match="does not match"):
        plan_script(changed, story)


def test_shrimp_provider_step1_end_to_end_invalidation_and_rebuild():
    opportunity_id = None
    provider_id = None
    try:
        registered = register_shrimp_animation_provider()
        provider_id = registered["provider_id"]
        assert registered["provider_key"] == "shrimp_animation"
        assert registered["asset_class"] == "CONTENT_IP"
        assert len(registered["spec_sha256"]) == 64
        assert [stage.key for stage in SHRIMP_ANIMATION_SPEC.stages] == [
            "CONTENT_BRIEF",
            "STORY",
            "SCRIPT",
            "SCENE",
            "ASSETS",
            "VOICES",
            "ANIMATION",
            "RENDER",
            "QC",
        ]

        opportunity_id, proposal_id = _create_content_ip_proposal()
        brief = _brief()
        created = create_shrimp_animation_job(
            proposal_id,
            brief,
            requested_by="ci-shrimp",
        )
        job_id = created["job_id"]
        assert created["episode_id"] == brief.episode_id

        before = get_provider_job(job_id)
        assert before["job_status"] == "READY"
        assert before["external_side_effects"] == "DENY"
        assert before["production_execution_enabled"] is False
        assert before["publish_enabled"] is False
        before_status = {
            row["stage_key"]: row["stage_status"]
            for row in before["stages"]
        }
        assert before_status["CONTENT_BRIEF"] == "SUCCEEDED"
        assert all(
            before_status[key] == "PENDING"
            for key in [
                "STORY", "SCRIPT", "SCENE", "ASSETS", "VOICES",
                "ANIMATION", "RENDER", "QC",
            ]
        )

        first = run_deterministic_planning(job_id)
        assert first["next_stage"] == "ASSETS"
        assert first["external_side_effects"] == "DENY"
        assert first["publish_enabled"] is False
        assert len(first["story_sha256"]) == 64
        assert len(first["script_sha256"]) == 64
        assert len(first["scene_sha256"]) == 64

        planned = get_shrimp_animation_job(job_id)
        statuses = {
            row["stage_key"]: row["stage_status"]
            for row in planned["stages"]
        }
        assert statuses["CONTENT_BRIEF"] == "SUCCEEDED"
        assert statuses["STORY"] == "SUCCEEDED"
        assert statuses["SCRIPT"] == "SUCCEEDED"
        assert statuses["SCENE"] == "SUCCEEDED"
        assert all(
            statuses[key] == "PENDING"
            for key in ["ASSETS", "VOICES", "ANIMATION", "RENDER", "QC"]
        )
        meta = planned["shrimp_animation"]
        assert meta["story_sha256"] == first["story_sha256"]
        assert meta["script_sha256"] == first["script_sha256"]
        assert meta["scene_sha256"] == first["scene_sha256"]

        with engine.connect() as db:
            first_versions = dict(
                db.execute(
                    text("""
                      SELECT manifest_kind,MAX(manifest_version)
                      FROM production_provider_manifests
                      WHERE job_id=CAST(:job_id AS uuid)
                      GROUP BY manifest_kind
                    """),
                    {"job_id": job_id},
                ).all()
            )
        assert first_versions == {
            "content_brief": 1,
            "story_manifest": 1,
            "script_manifest": 1,
            "scene_manifest": 1,
        }

        repeated = run_deterministic_planning(job_id)
        assert repeated["story_sha256"] == first["story_sha256"]
        assert repeated["script_sha256"] == first["script_sha256"]
        assert repeated["scene_sha256"] == first["scene_sha256"]

        with engine.connect() as db:
            manifest_count = db.execute(
                text("""
                  SELECT COUNT(*)
                  FROM production_provider_manifests
                  WHERE job_id=CAST(:job_id AS uuid)
                """),
                {"job_id": job_id},
            ).scalar_one()
        assert manifest_count == 4

        changed_brief = _brief(
            "A revised harbor incident sends the inspector to a warehouse, "
            "where a missing manifest changes the resolution and scene plan."
        )
        updated = update_shrimp_content_brief(
            job_id,
            changed_brief,
            actor="ci-shrimp",
        )
        assert updated["invalidated_from"] == "CONTENT_BRIEF"

        invalidated = get_shrimp_animation_job(job_id)
        invalidated_status = {
            row["stage_key"]: row["stage_status"]
            for row in invalidated["stages"]
        }
        assert invalidated_status["CONTENT_BRIEF"] == "SUCCEEDED"
        assert all(
            invalidated_status[key] == "STALE"
            for key in [
                "STORY", "SCRIPT", "SCENE", "ASSETS", "VOICES",
                "ANIMATION", "RENDER", "QC",
            ]
        )
        assert invalidated["shrimp_animation"]["story_sha256"] is None
        assert invalidated["shrimp_animation"]["script_sha256"] is None
        assert invalidated["shrimp_animation"]["scene_sha256"] is None

        rebuilt = run_deterministic_planning(job_id)
        assert rebuilt["story_sha256"] != first["story_sha256"]
        assert rebuilt["script_sha256"] != first["script_sha256"]
        assert rebuilt["scene_sha256"] != first["scene_sha256"]

        with engine.connect() as db:
            versions = dict(
                db.execute(
                    text("""
                      SELECT manifest_kind,MAX(manifest_version)
                      FROM production_provider_manifests
                      WHERE job_id=CAST(:job_id AS uuid)
                      GROUP BY manifest_kind
                    """),
                    {"job_id": job_id},
                ).all()
            )
            unsafe = db.execute(
                text("""
                  SELECT COUNT(*)
                  FROM production_provider_jobs
                  WHERE id=CAST(:job_id AS uuid)
                    AND (
                      external_side_effects<>'DENY'
                      OR production_execution_enabled=true
                      OR publish_enabled=true
                    )
                """),
                {"job_id": job_id},
            ).scalar_one()
        assert versions == {
            "content_brief": 2,
            "story_manifest": 2,
            "script_manifest": 2,
            "scene_manifest": 2,
        }
        assert unsafe == 0
    finally:
        with engine.begin() as db:
            if opportunity_id is not None:
                db.execute(
                    text("""
                      DELETE FROM digital_asset_opportunities
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": str(opportunity_id)},
                )
            if provider_id is not None:
                db.execute(
                    text("""
                      DELETE FROM production_provider_definitions
                      WHERE id=CAST(:id AS uuid)
                    """),
                    {"id": str(provider_id)},
                )
