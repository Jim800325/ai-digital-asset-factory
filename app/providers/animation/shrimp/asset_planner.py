from __future__ import annotations

from app.providers.animation.models import (
    AssetPlan,
    AssetRequirement,
    CapabilityRequirement,
    ContentBrief,
    SceneManifest,
    manifest_sha256,
)


ASSET_PLANNER_VERSION = "asset-planner-v0.1-deterministic"


def _asset_status(asset: dict | None) -> str:
    if asset is None:
        return "GENERATION_REQUIRED"
    rights = asset.get("usage_rights")
    if rights == "APPROVED":
        return "REUSE_READY"
    if rights == "BLOCKED":
        return "BLOCKED"
    return "RIGHTS_REVIEW"


def plan_assets(
    brief: ContentBrief,
    scene: SceneManifest,
    registry_snapshot: dict,
) -> AssetPlan:
    if not isinstance(brief, ContentBrief):
        brief = ContentBrief.model_validate(brief)
    if not isinstance(scene, SceneManifest):
        scene = SceneManifest.model_validate(scene)
    if scene.episode_id != brief.episode_id:
        raise ValueError("scene and brief episode_id mismatch")

    character_rows = {
        row["character_id"]: row
        for row in registry_snapshot.get("characters", [])
    }
    background_rows = {
        row["background_id"]: row
        for row in registry_snapshot.get("backgrounds", [])
    }
    action_rows = {
        row["action_key"]: row
        for row in registry_snapshot.get("actions", [])
    }
    camera_rows = {
        row["camera_key"]: row
        for row in registry_snapshot.get("cameras", [])
    }

    requested_variants: dict[str, set[str]] = {}
    requested_backgrounds: set[str] = set()
    requested_actions: set[str] = set()
    requested_cameras: set[str] = set()
    for render_scene in scene.scenes:
        requested_backgrounds.add(render_scene.background_id)
        for action in render_scene.characters:
            requested_variants.setdefault(action.character_id, set()).add(
                action.asset_variant
            )
            requested_actions.add(action.action)
        for cue in render_scene.camera:
            requested_cameras.add(cue.type)

    requirements: list[AssetRequirement] = []
    for character in sorted(brief.characters, key=lambda item: item.character_id):
        row = character_rows.get(character.character_id)
        variants = sorted(requested_variants.get(character.character_id, {"idle"}))
        variant_map = {
            item["variant_name"]: item.get("asset")
            for item in ((row or {}).get("variants") or [])
        }
        for variant in variants:
            asset = None
            if row:
                if variant == "idle":
                    asset = row.get("base_asset")
                if asset is None:
                    asset = variant_map.get(variant)
            requirements.append(
                AssetRequirement(
                    requirement_key=f"character:{character.character_id}:{variant}",
                    asset_kind="CHARACTER",
                    logical_id=character.character_id,
                    variant=variant,
                    status=_asset_status(asset),
                    reusable_asset=asset,
                )
            )

    for background_id in sorted(requested_backgrounds):
        row = background_rows.get(background_id)
        asset = (row or {}).get("asset")
        requirements.append(
            AssetRequirement(
                requirement_key=f"background:{background_id}",
                asset_kind="BACKGROUND",
                logical_id=background_id,
                status=_asset_status(asset),
                reusable_asset=asset,
            )
        )

    capabilities: list[CapabilityRequirement] = []
    for action_key in sorted(requested_actions):
        row = action_rows.get(action_key)
        capabilities.append(
            CapabilityRequirement(
                capability_kind="ACTION",
                capability_key=action_key,
                renderer_primitive=(row or {}).get("renderer_primitive"),
                status=(
                    "SUPPORTED"
                    if row and row.get("deterministic")
                    else "BLOCKED"
                ),
            )
        )
    for camera_key in sorted(requested_cameras):
        row = camera_rows.get(camera_key)
        capabilities.append(
            CapabilityRequirement(
                capability_kind="CAMERA",
                capability_key=camera_key,
                renderer_primitive=(row or {}).get("renderer_primitive"),
                status=(
                    "SUPPORTED"
                    if row and row.get("deterministic")
                    else "BLOCKED"
                ),
            )
        )

    reuse_ready = sum(item.status == "REUSE_READY" for item in requirements)
    generation_required = sum(
        item.status == "GENERATION_REQUIRED" for item in requirements
    )
    review_required = sum(
        item.status == "RIGHTS_REVIEW" for item in requirements
    )
    blocked = (
        sum(item.status == "BLOCKED" for item in requirements)
        + sum(item.status == "BLOCKED" for item in capabilities)
    )
    return AssetPlan(
        episode_id=brief.episode_id,
        scene_sha256=manifest_sha256(scene),
        registry_snapshot_sha256=registry_snapshot["snapshot_sha256"],
        requirements=requirements,
        capabilities=capabilities,
        reuse_ready_count=reuse_ready,
        generation_required_count=generation_required,
        review_required_count=review_required,
        blocked_count=blocked,
        ready_for_asset_execution=(review_required == 0 and blocked == 0),
    )
