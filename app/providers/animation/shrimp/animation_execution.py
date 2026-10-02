from __future__ import annotations

import hashlib
import json

from sqlalchemy import text

from app.db import engine
from app.production_provider_contract import (
    ProviderManifestEnvelope,
    complete_provider_stage,
    fail_provider_stage,
    get_provider_job,
    start_provider_stage,
    write_provider_manifest,
)
from app.providers.animation.models import (
    AnimationTimelineManifest,
    AssetArtifactManifest,
    RemotionCompositionPayload,
    SceneManifest,
    VoiceArtifactManifest,
    canonical_json,
)
from app.providers.animation.shrimp.adapters.remotion import (
    AnimationCompositionAdapter,
)
from app.providers.animation.shrimp.animation_planner import (
    plan_animation_timeline,
)


def _current_manifest(
    job_id,
    *,
    stage_key: str,
    manifest_kind: str,
) -> tuple[dict, str]:
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT content,content_sha256
              FROM production_provider_manifests
              WHERE job_id=CAST(:job_id AS uuid)
                AND stage_key=:stage_key
                AND manifest_kind=:manifest_kind
                AND is_current=true
              ORDER BY manifest_version DESC
              LIMIT 1
            """),
            {
                "job_id": job_id,
                "stage_key": stage_key,
                "manifest_kind": manifest_kind,
            },
        ).mappings().one_or_none()
    if row is None:
        raise RuntimeError(
            f"Current {stage_key}/{manifest_kind} manifest is missing"
        )
    return row["content"], row["content_sha256"]


def _current_plan_sha(job_id, plan_kind: str) -> str:
    with engine.connect() as db:
        value = db.execute(
            text("""
              SELECT content_sha256
              FROM shrimp_animation_resource_plans
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND plan_kind=:plan_kind
                AND plan_status='CURRENT'
              ORDER BY plan_version DESC
              LIMIT 1
            """),
            {"job_id": job_id, "plan_kind": plan_kind},
        ).scalar_one_or_none()
    if value is None:
        raise RuntimeError(f"Current {plan_kind} resource plan is missing")
    return value


def _stage_status(job: dict, key: str) -> str:
    return next(
        stage["stage_status"]
        for stage in job["stages"]
        if stage["stage_key"] == key
    )


def _validate_animation_inputs(job_id):
    job = get_provider_job(job_id)
    required = {
        "SCENE": "SUCCEEDED",
        "ASSETS": "SUCCEEDED",
        "VOICES": "SUCCEEDED",
    }
    for key, expected in required.items():
        actual = _stage_status(job, key)
        if actual != expected:
            raise RuntimeError(
                f"{key} must be {expected} before ANIMATION; got {actual}"
            )

    scene_payload, scene_sha = _current_manifest(
        job_id,
        stage_key="SCENE",
        manifest_kind="scene_manifest",
    )
    asset_payload, asset_sha = _current_manifest(
        job_id,
        stage_key="ASSETS",
        manifest_kind="asset_artifact_manifest",
    )
    voice_payload, voice_sha = _current_manifest(
        job_id,
        stage_key="VOICES",
        manifest_kind="voice_artifact_manifest",
    )

    scene = SceneManifest.model_validate(scene_payload)
    assets = AssetArtifactManifest.model_validate(asset_payload)
    voices = VoiceArtifactManifest.model_validate(voice_payload)

    if assets.asset_plan_sha256 != _current_plan_sha(job_id, "ASSET"):
        raise RuntimeError("ASSETS manifest is not bound to current ASSET plan")
    if voices.voice_plan_sha256 != _current_plan_sha(job_id, "VOICE"):
        raise RuntimeError("VOICES manifest is not bound to current VOICE plan")

    return job, scene, scene_sha, assets, asset_sha, voices, voice_sha


def _verify_remotion_payload(
    timeline: AnimationTimelineManifest,
    payload: RemotionCompositionPayload,
) -> str:
    expected_props = {
        "animation": timeline.model_dump(mode="json"),
    }
    if payload.props != expected_props:
        raise ValueError("Remotion adapter props do not match animation timeline")
    props_sha = hashlib.sha256(
        canonical_json(expected_props).encode("utf-8")
    ).hexdigest()
    if payload.props_sha256 != props_sha:
        raise ValueError("Remotion props SHA-256 mismatch")
    if len(payload.project_source_sha256) != 64:
        raise ValueError("Remotion project source SHA-256 is invalid")
    return props_sha


def _persist_composition(
    job_id,
    *,
    animation_manifest_sha256: str,
    scene_manifest_sha256: str,
    asset_manifest_sha256: str,
    voice_manifest_sha256: str,
    payload: RemotionCompositionPayload,
) -> dict:
    with engine.begin() as db:
        current = db.execute(
            text("""
              SELECT id,animation_manifest_sha256,props_sha256
              FROM shrimp_animation_compositions
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND composition_status='CURRENT'
              FOR UPDATE
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()

        if (
            current
            and current["animation_manifest_sha256"]
            == animation_manifest_sha256
            and current["props_sha256"] == payload.props_sha256
        ):
            return {
                "composition_id": str(current["id"]),
                "changed": False,
            }

        if current:
            db.execute(
                text("""
                  UPDATE shrimp_animation_compositions
                  SET composition_status='STALE',
                      superseded_at=COALESCE(superseded_at,now())
                  WHERE id=:id
                """),
                {"id": current["id"]},
            )

        composition_id = db.execute(
            text("""
              INSERT INTO shrimp_animation_compositions(
                provider_job_id,animation_manifest_sha256,
                scene_manifest_sha256,asset_manifest_sha256,
                voice_manifest_sha256,renderer_key,renderer_version,
                composition_id,project_source_sha256,props_uri,
                props_sha256,props_content,composition_status)
              VALUES(
                CAST(:job_id AS uuid),:animation_manifest_sha256,
                :scene_manifest_sha256,:asset_manifest_sha256,
                :voice_manifest_sha256,:renderer_key,:renderer_version,
                :composition_id,:project_source_sha256,:props_uri,
                :props_sha256,CAST(:props_content AS jsonb),'CURRENT')
              RETURNING id
            """),
            {
                "job_id": job_id,
                "animation_manifest_sha256": animation_manifest_sha256,
                "scene_manifest_sha256": scene_manifest_sha256,
                "asset_manifest_sha256": asset_manifest_sha256,
                "voice_manifest_sha256": voice_manifest_sha256,
                "renderer_key": payload.adapter_key,
                "renderer_version": payload.adapter_version,
                "composition_id": payload.composition_id,
                "project_source_sha256": payload.project_source_sha256,
                "props_uri": payload.props_uri,
                "props_sha256": payload.props_sha256,
                "props_content": canonical_json(payload.props),
            },
        ).scalar_one()

    return {
        "composition_id": str(composition_id),
        "changed": True,
    }


def execute_animation_stage(
    job_id,
    *,
    adapter: AnimationCompositionAdapter,
    actor: str = "shrimp-animation-worker",
) -> dict:
    (
        job,
        scene,
        scene_sha,
        assets,
        asset_sha,
        voices,
        voice_sha,
    ) = _validate_animation_inputs(job_id)

    if _stage_status(job, "ANIMATION") == "SUCCEEDED":
        current, manifest_sha = _current_manifest(
            job_id,
            stage_key="ANIMATION",
            manifest_kind="animation_timeline_manifest",
        )
        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "manifest_sha256": manifest_sha,
            "timeline_sha256": hashlib.sha256(
                canonical_json(current["timeline"]).encode("utf-8")
            ).hexdigest(),
            "replayed": True,
        }

    timeline = plan_animation_timeline(
        scene,
        assets,
        voices,
        scene_manifest_sha256=scene_sha,
        asset_manifest_sha256=asset_sha,
        voice_manifest_sha256=voice_sha,
    )

    start_provider_stage(job_id, "ANIMATION", actor=actor)
    try:
        remotion = adapter.prepare(timeline)
        props_sha = _verify_remotion_payload(timeline, remotion)

        manifest_payload = {
            "timeline": timeline.model_dump(mode="json"),
            "remotion": {
                "composition_id": remotion.composition_id,
                "props_sha256": remotion.props_sha256,
                "project_source_sha256": remotion.project_source_sha256,
                "adapter_key": remotion.adapter_key,
                "adapter_version": remotion.adapter_version,
            },
        }
        written = write_provider_manifest(
            job_id,
            "ANIMATION",
            ProviderManifestEnvelope(
                manifest_kind="animation_timeline_manifest",
                schema_version=timeline.schema_version,
                payload=manifest_payload,
            ),
            actor=actor,
        )
        composition = _persist_composition(
            job_id,
            animation_manifest_sha256=written["content_sha256"],
            scene_manifest_sha256=scene_sha,
            asset_manifest_sha256=asset_sha,
            voice_manifest_sha256=voice_sha,
            payload=remotion,
        )
        complete_provider_stage(job_id, "ANIMATION", actor=actor)

        with engine.begin() as db:
            db.execute(
                text("""
                  UPDATE shrimp_animation_jobs
                  SET animation_manifest_sha256=:animation_sha,
                      remotion_props_sha256=:props_sha,
                      updated_at=now()
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                """),
                {
                    "job_id": job_id,
                    "animation_sha": written["content_sha256"],
                    "props_sha": props_sha,
                },
            )

        return {
            "job_id": str(job_id),
            "stage_status": "SUCCEEDED",
            "manifest_sha256": written["content_sha256"],
            "timeline_sha256": hashlib.sha256(
                canonical_json(
                    timeline.model_dump(mode="json")
                ).encode("utf-8")
            ).hexdigest(),
            "remotion_props_sha256": props_sha,
            "composition_record_id": composition["composition_id"],
            "composition_changed": composition["changed"],
            "total_duration_frames": timeline.total_duration_frames,
            "scene_count": len(timeline.scenes),
            "next_stage": "RENDER",
            "replayed": False,
        }
    except Exception as exc:
        fail_provider_stage(
            job_id,
            "ANIMATION",
            str(exc),
            retryable=not isinstance(
                exc,
                (ValueError, PermissionError, LookupError),
            ),
            actor=actor,
        )
        raise


def list_animation_compositions(
    job_id,
    *,
    include_stale: bool = False,
) -> list[dict]:
    sql = """
      SELECT id,animation_manifest_sha256,scene_manifest_sha256,
             asset_manifest_sha256,voice_manifest_sha256,
             renderer_key,renderer_version,composition_id,
             project_source_sha256,props_uri,props_sha256,
             props_content,composition_status,created_at,superseded_at
      FROM shrimp_animation_compositions
      WHERE provider_job_id=CAST(:job_id AS uuid)
    """
    if not include_stale:
        sql += " AND composition_status='CURRENT'"
    sql += " ORDER BY created_at DESC"
    with engine.connect() as db:
        return [
            dict(row)
            for row in db.execute(
                text(sql),
                {"job_id": job_id},
            ).mappings().all()
        ]
