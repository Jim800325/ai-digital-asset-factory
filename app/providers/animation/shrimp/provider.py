from __future__ import annotations

import hashlib

from sqlalchemy import text

from app.db import engine
from app.production_provider_contract import (
    ProviderManifestEnvelope,
    complete_provider_stage,
    create_provider_job,
    get_provider_job,
    invalidate_provider_stage,
    start_provider_stage,
    write_provider_manifest,
)
from app.providers.animation.models import (
    ContentBrief,
    SceneManifest,
    ScriptManifest,
    StoryManifest,
    manifest_sha256,
)
from app.providers.animation.registry import register_shrimp_animation_provider
from app.providers.animation.shrimp.scene_planner import plan_scenes
from app.providers.animation.shrimp.script_planner import plan_script
from app.providers.animation.shrimp.story_planner import plan_story


PLANNING_VERSION = "shrimp-planning-v0.1-deterministic"


def _seed_for(brief: ContentBrief) -> str:
    return hashlib.sha256(
        (
            "shrimp_animation\n"
            + PLANNING_VERSION
            + "\n"
            + manifest_sha256(brief)
        ).encode("utf-8")
    ).hexdigest()


def _current_manifest_payload(job_id, stage_key: str, manifest_kind: str):
    with engine.connect() as db:
        row = db.execute(
            text("""
              SELECT content
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
        ).scalar_one_or_none()
    return row


def create_shrimp_animation_job(
    proposal_id,
    brief: ContentBrief,
    *,
    requested_by: str = "shrimp-animation",
) -> dict:
    if not isinstance(brief, ContentBrief):
        brief = ContentBrief.model_validate(brief)
    register_shrimp_animation_provider()
    created = create_provider_job(
        "shrimp_animation",
        proposal_id,
        requested_by=requested_by,
    )
    job_id = created["job_id"]
    brief_sha = manifest_sha256(brief)

    with engine.connect() as db:
        existing = db.execute(
            text("""
              SELECT episode_id,brief_sha256
              FROM shrimp_animation_jobs
              WHERE provider_job_id=CAST(:job_id AS uuid)
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
    if existing:
        if (
            existing["episode_id"] != brief.episode_id
            or existing["brief_sha256"] != brief_sha
        ):
            raise RuntimeError(
                "Existing provider job is bound to a different content brief"
            )
        return {
            **created,
            "episode_id": brief.episode_id,
            "brief_sha256": brief_sha,
        }

    start_provider_stage(job_id, "CONTENT_BRIEF", actor=requested_by)
    manifest = write_provider_manifest(
        job_id,
        "CONTENT_BRIEF",
        ProviderManifestEnvelope(
            manifest_kind="content_brief",
            schema_version=brief.schema_version,
            payload=brief.model_dump(mode="json"),
        ),
        actor=requested_by,
    )
    complete_provider_stage(job_id, "CONTENT_BRIEF", actor=requested_by)

    with engine.begin() as db:
        db.execute(
            text("""
              INSERT INTO shrimp_animation_jobs(
                provider_job_id,episode_id,schema_version,planning_version,
                deterministic_seed,brief_sha256)
              VALUES(
                CAST(:job_id AS uuid),:episode_id,:schema_version,:planning_version,
                :seed,:brief_sha256)
            """),
            {
                "job_id": job_id,
                "episode_id": brief.episode_id,
                "schema_version": brief.schema_version,
                "planning_version": PLANNING_VERSION,
                "seed": _seed_for(brief),
                "brief_sha256": manifest["content_sha256"],
            },
        )
    return {
        **created,
        "episode_id": brief.episode_id,
        "brief_sha256": manifest["content_sha256"],
    }


def update_shrimp_content_brief(
    job_id,
    brief: ContentBrief,
    *,
    actor: str = "shrimp-animation",
) -> dict:
    if not isinstance(brief, ContentBrief):
        brief = ContentBrief.model_validate(brief)
    with engine.connect() as db:
        meta = db.execute(
            text("""
              SELECT episode_id
              FROM shrimp_animation_jobs
              WHERE provider_job_id=CAST(:job_id AS uuid)
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
    if meta is None:
        raise LookupError("Shrimp animation job metadata not found")
    if meta["episode_id"] != brief.episode_id:
        raise ValueError("episode_id is immutable for a provider job")

    invalidate_provider_stage(
        job_id,
        "CONTENT_BRIEF",
        reason="content brief changed",
        actor=actor,
    )
    from app.providers.animation.shrimp.resource_planning import stale_resource_plans
    stale_resource_plans(job_id, reason="content brief changed")
    start_provider_stage(job_id, "CONTENT_BRIEF", actor=actor)
    manifest = write_provider_manifest(
        job_id,
        "CONTENT_BRIEF",
        ProviderManifestEnvelope(
            manifest_kind="content_brief",
            schema_version=brief.schema_version,
            payload=brief.model_dump(mode="json"),
        ),
        actor=actor,
    )
    complete_provider_stage(job_id, "CONTENT_BRIEF", actor=actor)
    with engine.begin() as db:
        db.execute(
            text("""
              UPDATE shrimp_animation_jobs
              SET deterministic_seed=:seed,
                  brief_sha256=:brief_sha256,
                  story_sha256=NULL,
                  script_sha256=NULL,
                  scene_sha256=NULL,
                  asset_plan_sha256=NULL,
                  voice_plan_sha256=NULL,
                  assets_manifest_sha256=NULL,
                  voices_manifest_sha256=NULL,
                  animation_manifest_sha256=NULL,
                  remotion_props_sha256=NULL,
                  render_artifact_sha256=NULL,
                  updated_at=now()
              WHERE provider_job_id=CAST(:job_id AS uuid)
            """),
            {
                "job_id": job_id,
                "seed": _seed_for(brief),
                "brief_sha256": manifest["content_sha256"],
            },
        )
    return {
        "job_id": str(job_id),
        "episode_id": brief.episode_id,
        "brief_sha256": manifest["content_sha256"],
        "invalidated_from": "CONTENT_BRIEF",
    }


def _run_stage(
    job_id,
    stage_key: str,
    manifest_kind: str,
    schema_version: str,
    payload: dict,
    *,
    actor: str,
) -> str:
    job = get_provider_job(job_id)
    stage = next(
        stage
        for stage in job["stages"]
        if stage["stage_key"] == stage_key
    )
    if stage["stage_status"] == "SUCCEEDED":
        current = _current_manifest_payload(job_id, stage_key, manifest_kind)
        if current is None:
            raise RuntimeError(
                f"{stage_key} succeeded without a current manifest"
            )
        return manifest_sha256(
            {
                "manifest_kind": manifest_kind,
                "schema_version": schema_version,
                "payload": current,
            }
        )
    if stage["stage_status"] not in {"PENDING", "STALE"}:
        raise RuntimeError(
            f"{stage_key} cannot run from {stage['stage_status']}"
        )

    start_provider_stage(job_id, stage_key, actor=actor)
    written = write_provider_manifest(
        job_id,
        stage_key,
        ProviderManifestEnvelope(
            manifest_kind=manifest_kind,
            schema_version=schema_version,
            payload=payload,
        ),
        actor=actor,
    )
    complete_provider_stage(job_id, stage_key, actor=actor)
    return written["content_sha256"]


def run_deterministic_planning(
    job_id,
    *,
    actor: str = "shrimp-animation-planner",
) -> dict:
    brief_payload = _current_manifest_payload(
        job_id,
        "CONTENT_BRIEF",
        "content_brief",
    )
    if brief_payload is None:
        raise RuntimeError("Current content brief manifest is missing")
    brief = ContentBrief.model_validate(brief_payload)

    story = plan_story(brief)
    story_sha = _run_stage(
        job_id,
        "STORY",
        "story_manifest",
        story.schema_version,
        story.model_dump(mode="json"),
        actor=actor,
    )

    script = plan_script(brief, story)
    script_sha = _run_stage(
        job_id,
        "SCRIPT",
        "script_manifest",
        script.schema_version,
        script.model_dump(mode="json"),
        actor=actor,
    )

    scene = plan_scenes(brief, script)
    scene_sha = _run_stage(
        job_id,
        "SCENE",
        "scene_manifest",
        scene.schema_version,
        scene.model_dump(mode="json"),
        actor=actor,
    )

    with engine.begin() as db:
        db.execute(
            text("""
              UPDATE shrimp_animation_jobs
              SET story_sha256=:story_sha256,
                  script_sha256=:script_sha256,
                  scene_sha256=:scene_sha256,
                  updated_at=now()
              WHERE provider_job_id=CAST(:job_id AS uuid)
            """),
            {
                "job_id": job_id,
                "story_sha256": story_sha,
                "script_sha256": script_sha,
                "scene_sha256": scene_sha,
            },
        )
    return {
        "job_id": str(job_id),
        "episode_id": brief.episode_id,
        "planning_version": PLANNING_VERSION,
        "story_sha256": story_sha,
        "script_sha256": script_sha,
        "scene_sha256": scene_sha,
        "next_stage": "ASSETS",
        "resource_planning_required": True,
        "external_side_effects": "DENY",
        "publish_enabled": False,
    }


def get_shrimp_animation_job(job_id) -> dict:
    job = get_provider_job(job_id)
    with engine.connect() as db:
        meta = db.execute(
            text("""
              SELECT episode_id,schema_version,planning_version,
                     deterministic_seed,brief_sha256,story_sha256,
                     script_sha256,scene_sha256,asset_plan_sha256,
                     voice_plan_sha256,assets_manifest_sha256,
                     voices_manifest_sha256,animation_manifest_sha256,
                     remotion_props_sha256,render_artifact_sha256,\n                     created_at,updated_at
              FROM shrimp_animation_jobs
              WHERE provider_job_id=CAST(:job_id AS uuid)
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
    if meta is None:
        raise LookupError("Shrimp animation job metadata not found")
    return {**job, "shrimp_animation": dict(meta)}
