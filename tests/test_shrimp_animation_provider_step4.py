from __future__ import annotations

import hashlib
import io
import wave

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.build_proposals import decide_build_proposal, ensure_build_proposal
from app.db import engine
from app.main import app
from app.production_provider_contract import get_provider_job
from app.providers.animation.artifact_verification import AdapterArtifactPayload
from app.providers.animation.asset_registry import (
    register_background,
    register_character,
    register_reusable_asset,
    register_voice_profile,
)
from app.providers.animation.models import (
    ArtifactProvenance,
    CharacterBrief,
    ContentBrief,
    RemotionCompositionPayload,
    canonical_json,
)
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.animation_execution import (
    execute_animation_stage,
    list_animation_compositions,
)
from app.providers.animation.shrimp.execution import (
    execute_asset_stage,
    execute_voice_stage,
)
from app.providers.animation.shrimp.provider import (
    create_shrimp_animation_job,
    get_shrimp_animation_job,
    run_deterministic_planning,
    update_shrimp_content_brief,
)
from app.providers.animation.shrimp.resource_planning import run_resource_planning


def _png(label: str) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + label.encode("utf-8")


def _wav(duration_ms: int = 900, sample_rate: int = 16000) -> bytes:
    frames = int(sample_rate * duration_ms / 1000)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(sample_rate)
        out.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


class FixtureResolver:
    def __init__(self, mapping: dict[str, bytes]):
        self.mapping = mapping

    def read_bytes(self, storage_uri: str) -> bytes:
        return self.mapping[storage_uri]


class NoGenerationAssetAdapter:
    adapter_key = "FIXTURE_NO_GENERATION"
    adapter_version = "ci-v1"

    def generate(self, requirement, *, prompt: str, seed: int, output_prefix: str):
        raise AssertionError(
            f"asset generation was not expected for {requirement.requirement_key}"
        )


class FixtureVoiceAdapter:
    adapter_key = "FIXTURE_TTS"
    adapter_version = "ci-v1"

    def __init__(self):
        self.calls = 0

    def synthesize(self, request, *, profile_metadata: dict):
        self.calls += 1
        content = _wav(900)
        request_sha = hashlib.sha256(
            canonical_json({
                "request_key": request.request_key,
                "profile_metadata": profile_metadata,
            }).encode("utf-8")
        ).hexdigest()
        return AdapterArtifactPayload(
            logical_key=request.voice_asset_id,
            artifact_kind="VOICE",
            content=content,
            storage_uri=f"fixture://step4/voice/{request.voice_asset_id}.wav",
            media_type="audio/wav",
            license_id="CI-VOICE-LICENSE",
            usage_rights="APPROVED",
            provenance=ArtifactProvenance(
                adapter_key=self.adapter_key,
                adapter_version=self.adapter_version,
                provider_request_id=f"voice-{request.request_key[:16]}",
                source_reference=profile_metadata["ref_audio_path"],
                source_type="FIXTURE",
                provenance="CI-only voice fixture; not a real TTS result",
                request_sha256=request_sha,
            ),
        )


class FixtureRemotionAdapter:
    adapter_key = "FIXTURE_REMOTION"
    adapter_version = "ci-v1"

    def __init__(self):
        self.calls = 0

    def prepare(self, timeline):
        self.calls += 1
        props = {"animation": timeline.model_dump(mode="json")}
        props_sha = hashlib.sha256(
            canonical_json(props).encode("utf-8")
        ).hexdigest()
        return RemotionCompositionPayload(
            composition_id="ShrimpAnimation",
            props_uri=f"fixture://step4/remotion/{props_sha}.json",
            props_sha256=props_sha,
            project_source_sha256="a" * 64,
            adapter_key=self.adapter_key,
            adapter_version=self.adapter_version,
            props=props,
        )


def _brief(premise: str | None = None) -> ContentBrief:
    return ContentBrief(
        episode_id="shrimp-step4-acceptance",
        title="港口时间线验收",
        premise=premise or (
            "A young inspector and a cautious adviser discover a shipping error "
            "and must resolve it before the market opens."
        ),
        audience="short-form story animation viewers",
        language="zh-CN",
        target_duration_ms=45000,
        scene_count=3,
        characters=[
            CharacterBrief(
                character_id="step4_hero",
                display_name="阿海",
                role="young inspector",
                voice_profile_id="step4_hero_voice",
            ),
            CharacterBrief(
                character_id="step4_advisor",
                display_name="老周",
                role="cautious adviser",
                voice_profile_id="step4_advisor_voice",
            ),
        ],
        background_hints=[
            "step4_harbor",
            "step4_warehouse",
            "step4_office",
        ],
        style_tags=["2d", "story", "comedy"],
    )


def _create_proposal():
    with engine.begin() as db:
        opportunity_id = db.execute(
            text("""
              INSERT INTO digital_asset_opportunities(
                fingerprint,title,asset_type,problem,target_customer,
                monetization_model,source_url,score,status,
                research_validation_score,build_readiness)
              VALUES(
                'shrimp-step4-opportunity-fixture',
                '确定性动画时间线内容包','CONTENT_IP',
                'manual timeline composition is slow',
                'short-form animation creators',
                'content licensing / channel package',
                'https://shrimp-step4.test/opportunity',
                95,'CANDIDATE',100,'BUILD_READY')
              RETURNING id
            """)
        ).scalar_one()
        db.execute(
            text("""
              INSERT INTO research_reports(
                opportunity_id,report_status,problem,buyer,
                existing_alternatives,evidence,monetization,
                build_complexity,risks,why_now,generator_version,observe_only)
              VALUES(
                :id,'GENERATED',
                'manual animation timeline composition is slow',
                'short-form animation creators',
                'manual editing',
                'two independent acceptance sources',
                'content licensing / channel package',
                'medium',
                'timeline drift and media provenance',
                'verified deterministic composition reduces repeated work',
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
    approved = decide_build_proposal(
        proposal["proposal_id"],
        decision="APPROVE",
        reason="shrimp Step 4 acceptance",
        actor="ci-human",
    )
    assert approved["proposal_status"] == "APPROVED"
    return opportunity_id, proposal["proposal_id"]


def _seed_registry() -> dict[str, bytes]:
    media = {
        "fixture://step4/hero.png": _png("hero"),
        "fixture://step4/advisor.png": _png("advisor"),
        "fixture://step4/harbor.png": _png("harbor"),
        "fixture://step4/warehouse.png": _png("warehouse"),
        "fixture://step4/office.png": _png("office"),
    }
    rows = [
        ("step4_hero_base", "CHARACTER_BASE", "fixture://step4/hero.png"),
        ("step4_advisor_base", "CHARACTER_BASE", "fixture://step4/advisor.png"),
        ("step4_harbor_asset", "BACKGROUND", "fixture://step4/harbor.png"),
        ("step4_warehouse_asset", "BACKGROUND", "fixture://step4/warehouse.png"),
        ("step4_office_asset", "BACKGROUND", "fixture://step4/office.png"),
    ]
    for key, kind, uri in rows:
        register_reusable_asset(
            asset_key=key,
            asset_kind=kind,
            storage_uri=uri,
            sha256=hashlib.sha256(media[uri]).hexdigest(),
            license_id="CI-ASSET-LICENSE",
            provenance="CI reusable image fixture",
            usage_rights="APPROVED",
        )

    for profile_id in ("step4_hero_voice", "step4_advisor_voice"):
        register_voice_profile(
            voice_profile_id=profile_id,
            provenance="CI synthetic voice profile",
            usage_rights="APPROVED",
            adapter_hint="gpt-sovits-local",
            source_type="SYNTHETIC",
            metadata={
                "ref_audio_path": f"fixture://step4/ref/{profile_id}.wav",
                "prompt_text": "CI reference",
                "prompt_lang": "zh-CN",
            },
        )

    register_character(
        character_id="step4_hero",
        display_name="阿海",
        base_asset_key="step4_hero_base",
        voice_profile_id="step4_hero_voice",
    )
    register_character(
        character_id="step4_advisor",
        display_name="老周",
        base_asset_key="step4_advisor_base",
        voice_profile_id="step4_advisor_voice",
    )
    register_background(
        background_id="step4_harbor",
        asset_key="step4_harbor_asset",
    )
    register_background(
        background_id="step4_warehouse",
        asset_key="step4_warehouse_asset",
    )
    register_background(
        background_id="step4_office",
        asset_key="step4_office_asset",
    )
    return media


def _cleanup_registry():
    with engine.begin() as db:
        db.execute(text(
            "DELETE FROM animation_character_registry "
            "WHERE character_id LIKE 'step4_%'"
        ))
        db.execute(text(
            "DELETE FROM animation_background_registry "
            "WHERE background_id LIKE 'step4_%'"
        ))
        db.execute(text(
            "DELETE FROM animation_reusable_assets "
            "WHERE asset_key LIKE 'step4_%'"
        ))
        db.execute(text(
            "DELETE FROM animation_voice_profiles "
            "WHERE voice_profile_id LIKE 'step4_%'"
        ))


def test_step4_animation_timeline_remotion_contract_and_invalidation():
    opportunity_id = None
    try:
        register_shrimp_animation_provider()
        reusable_media = _seed_registry()
        opportunity_id, proposal_id = _create_proposal()

        created = create_shrimp_animation_job(
            proposal_id,
            _brief(),
            requested_by="ci-shrimp-step4",
        )
        job_id = created["job_id"]
        run_deterministic_planning(job_id)
        resource = run_resource_planning(job_id)
        assert resource["asset_plan"]["reuse_ready_count"] == 5
        assert resource["asset_plan"]["generation_required_count"] == 0
        assert resource["voice_plan"]["ready_for_synthesis"] is True

        execute_asset_stage(
            job_id,
            adapter=NoGenerationAssetAdapter(),
            resolver=FixtureResolver(reusable_media),
            actor="ci-assets-step4",
        )
        voice_adapter = FixtureVoiceAdapter()
        execute_voice_stage(
            job_id,
            adapter=voice_adapter,
            actor="ci-voices-step4",
        )
        assert voice_adapter.calls == 6

        remotion = FixtureRemotionAdapter()
        result = execute_animation_stage(
            job_id,
            adapter=remotion,
            actor="ci-animation-step4",
        )
        assert result["stage_status"] == "SUCCEEDED"
        assert result["scene_count"] == 3
        assert result["total_duration_frames"] == 1350
        assert result["next_stage"] == "RENDER"
        assert remotion.calls == 1

        job = get_provider_job(job_id)
        statuses = {
            row["stage_key"]: row["stage_status"]
            for row in job["stages"]
        }
        assert statuses["ASSETS"] == "SUCCEEDED"
        assert statuses["VOICES"] == "SUCCEEDED"
        assert statuses["ANIMATION"] == "SUCCEEDED"
        assert statuses["RENDER"] == "PENDING"
        assert statuses["QC"] == "PENDING"
        assert job["production_execution_enabled"] is False
        assert job["publish_enabled"] is False

        shrimp = get_shrimp_animation_job(job_id)["shrimp_animation"]
        assert shrimp["animation_manifest_sha256"] == result["manifest_sha256"]
        assert shrimp["remotion_props_sha256"] == result["remotion_props_sha256"]

        with engine.connect() as db:
            animation = db.execute(
                text("""
                  SELECT content
                  FROM production_provider_manifests
                  WHERE job_id=CAST(:job_id AS uuid)
                    AND stage_key='ANIMATION'
                    AND manifest_kind='animation_timeline_manifest'
                    AND is_current=true
                """),
                {"job_id": job_id},
            ).scalar_one()
        timeline = animation["timeline"]
        assert timeline["total_duration_frames"] == 1350
        assert [
            (scene["start_frame"], scene["end_frame"])
            for scene in timeline["scenes"]
        ] == [(0, 450), (450, 900), (900, 1350)]
        assert all(len(scene["audio"]) == 2 for scene in timeline["scenes"])
        assert all(len(scene["subtitles"]) == 2 for scene in timeline["scenes"])
        assert all(len(scene["camera"]) >= 1 for scene in timeline["scenes"])
        assert all(
            cue["media"]["sha256"]
            for scene in timeline["scenes"]
            for cue in scene["audio"]
        )

        current = list_animation_compositions(job_id)
        assert len(current) == 1
        assert current[0]["renderer_key"] == "FIXTURE_REMOTION"
        assert current[0]["props_sha256"] == result["remotion_props_sha256"]
        assert current[0]["composition_status"] == "CURRENT"

        replay = execute_animation_stage(
            job_id,
            adapter=remotion,
            actor="ci-animation-step4",
        )
        assert replay["replayed"] is True
        assert replay["timeline_sha256"] == result["timeline_sha256"]
        assert remotion.calls == 1

        with pytest.raises(Exception):
            with engine.begin() as db:
                db.execute(
                    text("""
                      UPDATE shrimp_animation_compositions
                      SET props_sha256=repeat('0',64)
                      WHERE provider_job_id=CAST(:job_id AS uuid)
                        AND composition_status='CURRENT'
                    """),
                    {"job_id": job_id},
                )

        client = TestClient(app)
        response = client.get(
            f"/v1/shrimp-animation/jobs/{job_id}/compositions"
        )
        assert response.status_code == 200
        assert len(response.json()) == 1

        changed = _brief(
            "A revised harbor incident changes the dialogue and scene objectives, "
            "which must invalidate every verified downstream composition."
        )
        update_shrimp_content_brief(
            job_id,
            changed,
            actor="ci-shrimp-step4",
        )
        invalidated = get_provider_job(job_id)
        invalidated_status = {
            row["stage_key"]: row["stage_status"]
            for row in invalidated["stages"]
        }
        assert invalidated_status["ANIMATION"] == "STALE"
        assert invalidated_status["RENDER"] == "STALE"
        assert invalidated_status["QC"] == "STALE"
        assert list_animation_compositions(job_id) == []
        history = list_animation_compositions(job_id, include_stale=True)
        assert len(history) == 1
        assert history[0]["composition_status"] == "STALE"
        stale_meta = get_shrimp_animation_job(job_id)["shrimp_animation"]
        assert stale_meta["animation_manifest_sha256"] is None
        assert stale_meta["remotion_props_sha256"] is None
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
