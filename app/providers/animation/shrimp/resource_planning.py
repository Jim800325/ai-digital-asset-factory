from __future__ import annotations

import json

from sqlalchemy import text

from app.db import engine
from app.providers.animation.asset_registry import (
    load_asset_registry_snapshot,
    load_voice_registry_snapshot,
)
from app.providers.animation.models import (
    AssetPlan,
    ContentBrief,
    SceneManifest,
    VoicePlan,
    manifest_sha256,
)
from app.providers.animation.shrimp.asset_planner import (
    ASSET_PLANNER_VERSION,
    plan_assets,
)
from app.providers.animation.shrimp.voice_planner import (
    VOICE_PLANNER_VERSION,
    plan_voices,
)


def _current_manifest_payload(job_id, stage_key: str, manifest_kind: str):
    with engine.connect() as db:
        return db.execute(
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


def _persist_plan(
    job_id,
    *,
    plan_kind: str,
    schema_version: str,
    planner_version: str,
    source_manifest_sha256: str,
    registry_snapshot_sha256: str,
    content: dict,
) -> dict:
    content_sha256 = manifest_sha256(content)
    with engine.begin() as db:
        current = db.execute(
            text("""
              SELECT id,plan_version,content_sha256
              FROM shrimp_animation_resource_plans
              WHERE provider_job_id=CAST(:job_id AS uuid)
                AND plan_kind=:plan_kind
                AND plan_status='CURRENT'
              FOR UPDATE
            """),
            {"job_id": job_id, "plan_kind": plan_kind},
        ).mappings().one_or_none()

        if current and current["content_sha256"] == content_sha256:
            return {
                "plan_kind": plan_kind,
                "plan_version": int(current["plan_version"]),
                "content_sha256": content_sha256,
                "changed": False,
            }

        latest_version = int(
            db.execute(
                text("""
                  SELECT COALESCE(MAX(plan_version),0)
                  FROM shrimp_animation_resource_plans
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                    AND plan_kind=:plan_kind
                """),
                {"job_id": job_id, "plan_kind": plan_kind},
            ).scalar_one()
        )
        if current:
            db.execute(
                text("""
                  UPDATE shrimp_animation_resource_plans
                  SET plan_status='STALE',superseded_at=now()
                  WHERE id=:id
                """),
                {"id": current["id"]},
            )

        version = latest_version + 1
        db.execute(
            text("""
              INSERT INTO shrimp_animation_resource_plans(
                provider_job_id,plan_kind,plan_version,schema_version,
                planner_version,source_manifest_sha256,
                registry_snapshot_sha256,content,content_sha256,plan_status)
              VALUES(
                CAST(:job_id AS uuid),:plan_kind,:plan_version,:schema_version,
                :planner_version,:source_manifest_sha256,
                :registry_snapshot_sha256,CAST(:content AS jsonb),
                :content_sha256,'CURRENT')
            """),
            {
                "job_id": job_id,
                "plan_kind": plan_kind,
                "plan_version": version,
                "schema_version": schema_version,
                "planner_version": planner_version,
                "source_manifest_sha256": source_manifest_sha256,
                "registry_snapshot_sha256": registry_snapshot_sha256,
                "content": json.dumps(
                    content,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                "content_sha256": content_sha256,
            },
        )
        column = (
            "asset_plan_sha256"
            if plan_kind == "ASSET"
            else "voice_plan_sha256"
        )
        db.execute(
            text(
                f"""
                  UPDATE shrimp_animation_jobs
                  SET {column}=:sha,updated_at=now()
                  WHERE provider_job_id=CAST(:job_id AS uuid)
                """
            ),
            {"job_id": job_id, "sha": content_sha256},
        )
    return {
        "plan_kind": plan_kind,
        "plan_version": version,
        "content_sha256": content_sha256,
        "changed": True,
    }


def stale_resource_plans(job_id, *, reason: str = "upstream changed") -> int:
    with engine.begin() as db:
        changed = int(
            db.execute(
                text("""
                  WITH changed AS (
                    UPDATE shrimp_animation_resource_plans
                    SET plan_status='STALE',superseded_at=COALESCE(superseded_at,now())
                    WHERE provider_job_id=CAST(:job_id AS uuid)
                      AND plan_status='CURRENT'
                    RETURNING id
                  )
                  SELECT COUNT(*) FROM changed
                """),
                {"job_id": job_id},
            ).scalar_one()
        )
        db.execute(
            text("""
              UPDATE shrimp_animation_jobs
              SET asset_plan_sha256=NULL,
                  voice_plan_sha256=NULL,
                  updated_at=now()
              WHERE provider_job_id=CAST(:job_id AS uuid)
            """),
            {"job_id": job_id},
        )
        if changed:
            db.execute(
                text("""
                  INSERT INTO production_provider_events(
                    job_id,event_type,actor,payload)
                  VALUES(
                    CAST(:job_id AS uuid),'RESOURCE_PLANS_STALE',
                    'shrimp-resource-planner',CAST(:payload AS jsonb))
                """),
                {
                    "job_id": job_id,
                    "payload": json.dumps(
                        {
                            "reason": reason[:1000],
                            "count": changed,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                },
            )
    return changed


def run_resource_planning(job_id) -> dict:
    with engine.connect() as db:
        job = db.execute(
            text("""
              SELECT j.job_status,p.provider_key
              FROM production_provider_jobs j
              JOIN production_provider_definitions p ON p.id=j.provider_id
              WHERE j.id=CAST(:job_id AS uuid)
            """),
            {"job_id": job_id},
        ).mappings().one_or_none()
        if job is None:
            raise LookupError("Production provider job not found")
        if job["provider_key"] != "shrimp_animation":
            raise RuntimeError("Resource planner only accepts shrimp_animation jobs")
        if job["job_status"] in {"FAILED","BLOCKED","STALE","CANCELLED"}:
            raise RuntimeError("Shrimp animation job is not resource-plannable")
        stage_rows = {
            row["stage_key"]: row["stage_status"]
            for row in db.execute(
                text("""
                  SELECT stage_key,stage_status
                  FROM production_provider_job_stages
                  WHERE job_id=CAST(:job_id AS uuid)
                    AND stage_key=ANY(:keys)
                """),
                {
                    "job_id": job_id,
                    "keys": ["SCENE", "ASSETS", "VOICES"],
                },
            ).mappings().all()
        }
        if stage_rows.get("SCENE") != "SUCCEEDED":
            raise RuntimeError("SCENE stage must be SUCCEEDED before resource planning")
        for stage_key in ("ASSETS", "VOICES"):
            if stage_rows.get(stage_key) not in {"PENDING", "STALE"}:
                raise RuntimeError(
                    f"{stage_key} has already entered execution; resource planning is frozen"
                )

    brief_payload = _current_manifest_payload(
        job_id,
        "CONTENT_BRIEF",
        "content_brief",
    )
    scene_payload = _current_manifest_payload(
        job_id,
        "SCENE",
        "scene_manifest",
    )
    if brief_payload is None:
        raise RuntimeError("Current content brief manifest is missing")
    if scene_payload is None:
        raise RuntimeError("Current scene manifest is missing")

    brief = ContentBrief.model_validate(brief_payload)
    scene = SceneManifest.model_validate(scene_payload)
    character_ids = sorted({
        action.character_id
        for render_scene in scene.scenes
        for action in render_scene.characters
    })
    background_ids = sorted({
        render_scene.background_id
        for render_scene in scene.scenes
    })
    action_keys = sorted({
        action.action
        for render_scene in scene.scenes
        for action in render_scene.characters
    })
    camera_keys = sorted({
        cue.type
        for render_scene in scene.scenes
        for cue in render_scene.camera
    })
    profile_ids = sorted({
        character.voice_profile_id
        for character in brief.characters
    })

    asset_snapshot = load_asset_registry_snapshot(
        character_ids=character_ids,
        background_ids=background_ids,
        action_keys=action_keys,
        camera_keys=camera_keys,
    )
    voice_snapshot = load_voice_registry_snapshot(
        character_ids=character_ids,
        requested_profile_ids=profile_ids,
    )

    asset_plan: AssetPlan = plan_assets(
        brief,
        scene,
        asset_snapshot,
    )
    voice_plan: VoicePlan = plan_voices(
        brief,
        scene,
        voice_snapshot,
    )
    scene_sha = manifest_sha256(scene)
    asset_result = _persist_plan(
        job_id,
        plan_kind="ASSET",
        schema_version=asset_plan.schema_version,
        planner_version=ASSET_PLANNER_VERSION,
        source_manifest_sha256=scene_sha,
        registry_snapshot_sha256=asset_snapshot["snapshot_sha256"],
        content=asset_plan.model_dump(mode="json"),
    )
    voice_result = _persist_plan(
        job_id,
        plan_kind="VOICE",
        schema_version=voice_plan.schema_version,
        planner_version=VOICE_PLANNER_VERSION,
        source_manifest_sha256=scene_sha,
        registry_snapshot_sha256=voice_snapshot["snapshot_sha256"],
        content=voice_plan.model_dump(mode="json"),
    )
    return {
        "job_id": str(job_id),
        "asset_plan": {
            **asset_result,
            "reuse_ready_count": asset_plan.reuse_ready_count,
            "generation_required_count": asset_plan.generation_required_count,
            "review_required_count": asset_plan.review_required_count,
            "blocked_count": asset_plan.blocked_count,
            "ready_for_asset_execution": asset_plan.ready_for_asset_execution,
        },
        "voice_plan": {
            **voice_result,
            "ready_count": voice_plan.ready_count,
            "profile_required_count": voice_plan.profile_required_count,
            "review_required_count": voice_plan.review_required_count,
            "adapter_required_count": voice_plan.adapter_required_count,
            "blocked_count": voice_plan.blocked_count,
            "ready_for_synthesis": voice_plan.ready_for_synthesis,
        },
        "assets_stage_executed": False,
        "voices_stage_executed": False,
        "external_side_effects": "DENY",
    }


def list_resource_plans(job_id, *, include_stale: bool = False) -> list[dict]:
    sql = """
      SELECT plan_kind,plan_version,schema_version,planner_version,
             source_manifest_sha256,registry_snapshot_sha256,
             content,content_sha256,plan_status,created_at,superseded_at
      FROM shrimp_animation_resource_plans
      WHERE provider_job_id=CAST(:job_id AS uuid)
    """
    if not include_stale:
        sql += " AND plan_status='CURRENT'"
    sql += " ORDER BY plan_kind,plan_version DESC"
    with engine.connect() as db:
        return [
            dict(row)
            for row in db.execute(
                text(sql),
                {"job_id": job_id},
            ).mappings().all()
        ]
