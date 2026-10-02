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
from app.providers.animation.artifact_verification import (
    AdapterArtifactPayload,
    verify_artifact_payload,
)
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
)
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.adapters.comfyui import ComfyUIAssetAdapter
from app.providers.animation.shrimp.adapters.gptsovits import GPTSoVITSAdapter
from app.providers.animation.shrimp.execution import (
    execute_asset_stage,
    execute_voice_stage,
    list_shrimp_artifacts,
)
from app.providers.animation.shrimp.provider import (
    create_shrimp_animation_job,
    get_shrimp_animation_job,
    run_deterministic_planning,
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
        self.calls = 0

    def read_bytes(self, storage_uri: str) -> bytes:
        self.calls += 1
        try:
            return self.mapping[storage_uri]
        except KeyError as exc:
            raise FileNotFoundError(storage_uri) from exc


class FakeComfyUIAdapter:
    adapter_key = "FIXTURE_COMFYUI"
    adapter_version = "ci-v1"

    def __init__(self):
        self.calls = 0

    def generate(self, requirement, *, prompt: str, seed: int, output_prefix: str):
        self.calls += 1
        content = _png(
            f"{requirement.requirement_key}|{prompt}|{seed}|{output_prefix}"
        )
        request_sha = hashlib.sha256(
            f"{prompt}|{seed}|{output_prefix}".encode("utf-8")
        ).hexdigest()
        return AdapterArtifactPayload(
            logical_key=requirement.requirement_key,
            artifact_kind=(
                "BACKGROUND"
                if requirement.asset_kind == "BACKGROUND"
                else "CHARACTER"
            ),
            content=content,
            storage_uri=f"fixture://generated/{output_prefix}.png",
            media_type="image/png",
            license_id="CI-GENERATED-ASSET",
            usage_rights="APPROVED",
            provenance=ArtifactProvenance(
                adapter_key=self.adapter_key,
                adapter_version=self.adapter_version,
                provider_request_id=f"asset-{self.calls}",
                source_reference="fixture://comfyui",
                source_type="FIXTURE",
                provenance="CI-only generated image fixture; not a real ComfyUI output",
                request_sha256=request_sha,
            ),
        )


class FakeVoiceAdapter:
    adapter_key = "FIXTURE_TTS"
    adapter_version = "ci-v1"

    def __init__(self):
        self.calls = 0

    def synthesize(self, request, *, profile_metadata: dict):
        self.calls += 1
        content = _wav(900 + self.calls * 10)
        request_sha = hashlib.sha256(
            (
                request.request_key
                + "|"
                + str(profile_metadata.get("ref_audio_path") or "")
            ).encode("utf-8")
        ).hexdigest()
        return AdapterArtifactPayload(
            logical_key=request.voice_asset_id,
            artifact_kind="VOICE",
            content=content,
            storage_uri=f"fixture://tts/{request.voice_asset_id}.wav",
            media_type="audio/wav",
            license_id="CI-GENERATED-VOICE",
            usage_rights="APPROVED",
            provenance=ArtifactProvenance(
                adapter_key=self.adapter_key,
                adapter_version=self.adapter_version,
                provider_request_id=f"voice-{self.calls}",
                source_reference="fixture://tts",
                source_type="FIXTURE",
                provenance="CI-only synthesized voice fixture; not a real TTS output",
                request_sha256=request_sha,
            ),
        )


def _brief() -> ContentBrief:
    return ContentBrief(
        episode_id="shrimp-step3-acceptance",
        title="港口素材与语音验收",
        premise=(
            "A young inspector and a cautious adviser discover a shipping error "
            "and must resolve it before the market opens."
        ),
        audience="short-form story animation viewers",
        language="zh-CN",
        target_duration_ms=45000,
        scene_count=3,
        characters=[
            CharacterBrief(
                character_id="step3_hero",
                display_name="阿海",
                role="young inspector",
                voice_profile_id="step3_hero_voice",
            ),
            CharacterBrief(
                character_id="step3_advisor",
                display_name="老周",
                role="cautious adviser",
                voice_profile_id="step3_advisor_voice",
            ),
        ],
        background_hints=[
            "step3_harbor",
            "step3_warehouse",
            "step3_office",
        ],
        style_tags=["2d", "story", "comedy"],
    )


def _create_content_ip_proposal():
    with engine.begin() as db:
        opportunity_id = db.execute(
            text("""
              INSERT INTO digital_asset_opportunities(
                fingerprint,title,asset_type,problem,target_customer,
                monetization_model,source_url,score,status,
                research_validation_score,build_readiness)
              VALUES(
                'shrimp-step3-opportunity-fixture',
                '港口素材与语音动画内容包','CONTENT_IP',
                'manual animation asset and voice production is slow',
                'short-form animation creators',
                'content licensing / channel package',
                'https://shrimp-step3.test/opportunity',
                94,'CANDIDATE',100,'BUILD_READY')
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
                'manual animation asset and voice production is slow',
                'short-form animation creators',
                'manual asset generation and manual dubbing',
                'two independent acceptance sources',
                'content licensing / channel package',
                'medium',
                'asset provenance and voice rights',
                'verified reusable assets reduce repeated work',
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
    approved = decide_build_proposal(
        proposal["proposal_id"],
        decision="APPROVE",
        reason="shrimp Step 3 acceptance",
        actor="ci-human",
    )
    assert approved["proposal_status"] == "APPROVED"
    return opportunity_id, proposal["proposal_id"]


def _seed_registry():
    asset_bytes = {
        "fixture://step3/hero.png": _png("hero"),
        "fixture://step3/advisor.png": _png("advisor"),
        "fixture://step3/harbor.png": _png("harbor"),
        "fixture://step3/office.png": _png("office"),
    }
    assets = [
        ("step3_hero_base", "CHARACTER_BASE", "fixture://step3/hero.png"),
        ("step3_advisor_base", "CHARACTER_BASE", "fixture://step3/advisor.png"),
        ("step3_harbor_asset", "BACKGROUND", "fixture://step3/harbor.png"),
        ("step3_office_asset", "BACKGROUND", "fixture://step3/office.png"),
    ]
    for asset_key, kind, uri in assets:
        register_reusable_asset(
            asset_key=asset_key,
            asset_kind=kind,
            storage_uri=uri,
            sha256=hashlib.sha256(asset_bytes[uri]).hexdigest(),
            license_id="CI-APPROVED-ASSET",
            provenance="CI reusable artifact fixture",
            usage_rights="APPROVED",
        )

    for profile_id in ("step3_hero_voice", "step3_advisor_voice"):
        register_voice_profile(
            voice_profile_id=profile_id,
            provenance="CI synthetic voice profile fixture",
            usage_rights="APPROVED",
            adapter_hint="gpt-sovits-local",
            source_type="SYNTHETIC",
            metadata={
                "ref_audio_path": f"fixture://voice/{profile_id}.wav",
                "prompt_text": "CI reference",
                "prompt_lang": "zh-CN",
            },
        )

    register_character(
        character_id="step3_hero",
        display_name="阿海",
        base_asset_key="step3_hero_base",
        voice_profile_id="step3_hero_voice",
    )
    register_character(
        character_id="step3_advisor",
        display_name="老周",
        base_asset_key="step3_advisor_base",
        voice_profile_id="step3_advisor_voice",
    )
    register_background(
        background_id="step3_harbor",
        asset_key="step3_harbor_asset",
    )
    register_background(
        background_id="step3_office",
        asset_key="step3_office_asset",
    )
    return asset_bytes


def _cleanup_registry():
    with engine.begin() as db:
        db.execute(text(
            "DELETE FROM animation_character_registry "
            "WHERE character_id LIKE 'step3_%'"
        ))
        db.execute(text(
            "DELETE FROM animation_background_registry "
            "WHERE background_id LIKE 'step3_%'"
        ))
        db.execute(text(
            "DELETE FROM animation_reusable_assets "
            "WHERE asset_key LIKE 'step3_%'"
        ))
        db.execute(text(
            "DELETE FROM animation_voice_profiles "
            "WHERE voice_profile_id LIKE 'step3_%'"
        ))


def test_artifact_verifier_rejects_hash_mismatch_and_fake_image_signature():
    provenance = ArtifactProvenance(
        adapter_key="FIXTURE",
        adapter_version="v1",
        source_reference="fixture://test",
        source_type="FIXTURE",
        provenance="CI fixture",
        request_sha256="1" * 64,
    )
    payload = AdapterArtifactPayload(
        logical_key="background:test",
        artifact_kind="BACKGROUND",
        content=_png("valid"),
        storage_uri="fixture://test.png",
        media_type="image/png",
        license_id="CI",
        usage_rights="APPROVED",
        claimed_sha256="0" * 64,
        provenance=provenance,
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_artifact_payload(
            payload,
            source_mode="GENERATED",
            plan_sha256="2" * 64,
        )

    invalid = AdapterArtifactPayload(
        logical_key="background:test",
        artifact_kind="BACKGROUND",
        content=b"not-a-png",
        storage_uri="fixture://invalid.png",
        media_type="image/png",
        license_id="CI",
        usage_rights="APPROVED",
        provenance=provenance,
    )
    with pytest.raises(ValueError, match="signature"):
        verify_artifact_payload(
            invalid,
            source_mode="GENERATED",
            plan_sha256="2" * 64,
        )


def test_real_adapters_fail_closed_for_unapproved_public_hosts(tmp_path):
    workflow = tmp_path / "workflow.json"
    workflow.write_text(
        '{"1":{"inputs":{"text":"{{PROMPT}}"}}}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="allowlisted"):
        ComfyUIAssetAdapter(
            base_url="https://example.com",
            workflow_path=str(workflow),
            generated_license_id="TEST",
            generated_provenance="test",
        )
    with pytest.raises(ValueError, match="allowlisted"):
        GPTSoVITSAdapter(
            base_url="https://example.com",
            generated_license_id="TEST",
            generated_provenance="test",
        )


def test_step3_scene_to_verified_assets_and_voices():
    opportunity_id = None
    try:
        register_shrimp_animation_provider()
        reusable_bytes = _seed_registry()
        opportunity_id, proposal_id = _create_content_ip_proposal()

        created = create_shrimp_animation_job(
            proposal_id,
            _brief(),
            requested_by="ci-shrimp-step3",
        )
        job_id = created["job_id"]
        run_deterministic_planning(job_id)
        plans = run_resource_planning(job_id)
        assert plans["asset_plan"]["reuse_ready_count"] == 4
        assert plans["asset_plan"]["generation_required_count"] == 1
        assert plans["voice_plan"]["ready_for_synthesis"] is True

        resolver = FixtureResolver(reusable_bytes)
        asset_adapter = FakeComfyUIAdapter()
        voice_adapter = FakeVoiceAdapter()

        assets = execute_asset_stage(
            job_id,
            adapter=asset_adapter,
            resolver=resolver,
            actor="ci-assets",
        )
        assert assets["stage_status"] == "SUCCEEDED"
        assert assets["verified_artifacts"] == 5
        assert assets["reused_count"] == 4
        assert assets["generated_count"] == 1
        assert asset_adapter.calls == 1
        assert resolver.calls == 4

        voices = execute_voice_stage(
            job_id,
            adapter=voice_adapter,
            actor="ci-voices",
        )
        assert voices["stage_status"] == "SUCCEEDED"
        assert voices["verified_artifacts"] == 6
        assert voices["total_duration_ms"] > 0
        assert voice_adapter.calls == 6

        job = get_provider_job(job_id)
        stage_status = {
            row["stage_key"]: row["stage_status"]
            for row in job["stages"]
        }
        assert stage_status["ASSETS"] == "SUCCEEDED"
        assert stage_status["VOICES"] == "SUCCEEDED"
        assert stage_status["ANIMATION"] == "PENDING"
        assert job["production_execution_enabled"] is False
        assert job["publish_enabled"] is False

        shrimp = get_shrimp_animation_job(job_id)["shrimp_animation"]
        assert len(shrimp["assets_manifest_sha256"]) == 64
        assert len(shrimp["voices_manifest_sha256"]) == 64

        artifacts = list_shrimp_artifacts(job_id)
        assert len(artifacts) == 11
        assert sum(row["stage_key"] == "ASSETS" for row in artifacts) == 5
        assert sum(row["stage_key"] == "VOICES" for row in artifacts) == 6
        assert sum(row["source_mode"] == "REUSED" for row in artifacts) == 4
        assert sum(row["source_mode"] == "GENERATED" for row in artifacts) == 7
        assert all(row["verification_status"] == "VERIFIED" for row in artifacts)
        assert all(row["usage_rights"] == "APPROVED" for row in artifacts)

        asset_replay = execute_asset_stage(
            job_id,
            adapter=asset_adapter,
            resolver=resolver,
            actor="ci-assets",
        )
        voice_replay = execute_voice_stage(
            job_id,
            adapter=voice_adapter,
            actor="ci-voices",
        )
        assert asset_replay["replayed"] is True
        assert voice_replay["replayed"] is True
        assert asset_adapter.calls == 1
        assert voice_adapter.calls == 6
        assert resolver.calls == 4

        with engine.connect() as db:
            manifests = db.execute(
                text("""
                  SELECT stage_key,manifest_kind,content_sha256
                  FROM production_provider_manifests
                  WHERE job_id=CAST(:job_id AS uuid)
                    AND is_current=true
                    AND stage_key IN ('ASSETS','VOICES')
                  ORDER BY stage_key
                """),
                {"job_id": job_id},
            ).mappings().all()
            resource_events = db.execute(
                text("""
                  SELECT COUNT(*)
                  FROM production_provider_resource_events
                  WHERE job_id=CAST(:job_id AS uuid)
                    AND resource_type='NETWORK_BYTES'
                """),
                {"job_id": job_id},
            ).scalar_one()
        assert {
            (row["stage_key"], row["manifest_kind"])
            for row in manifests
        } == {
            ("ASSETS", "asset_artifact_manifest"),
            ("VOICES", "voice_artifact_manifest"),
        }
        assert resource_events == 7

        with pytest.raises(Exception):
            with engine.begin() as db:
                db.execute(
                    text("""
                      UPDATE shrimp_animation_artifacts
                      SET sha256=repeat('0',64)
                      WHERE id=(
                        SELECT id
                        FROM shrimp_animation_artifacts
                        WHERE provider_job_id=CAST(:job_id AS uuid)
                        LIMIT 1
                      )
                    """),
                    {"job_id": job_id},
                )

        client = TestClient(app)
        response = client.get(
            f"/v1/shrimp-animation/jobs/{job_id}/artifacts"
        )
        assert response.status_code == 200
        assert len(response.json()) == 11
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
