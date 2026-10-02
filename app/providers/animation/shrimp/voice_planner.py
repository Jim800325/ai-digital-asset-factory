from __future__ import annotations

import hashlib

from app.providers.animation.models import (
    ContentBrief,
    SceneManifest,
    VoicePlan,
    VoiceRequest,
    manifest_sha256,
)


VOICE_PLANNER_VERSION = "voice-planner-v0.1-deterministic"


def _request_key(
    episode_id: str,
    scene_id: str,
    line_id: str,
    text: str,
    voice_profile_id: str,
) -> str:
    return hashlib.sha256(
        (
            episode_id
            + "\n"
            + scene_id
            + "\n"
            + line_id
            + "\n"
            + text
            + "\n"
            + voice_profile_id
        ).encode("utf-8")
    ).hexdigest()


def plan_voices(
    brief: ContentBrief,
    scene: SceneManifest,
    registry_snapshot: dict,
) -> VoicePlan:
    if not isinstance(brief, ContentBrief):
        brief = ContentBrief.model_validate(brief)
    if not isinstance(scene, SceneManifest):
        scene = SceneManifest.model_validate(scene)
    if scene.episode_id != brief.episode_id:
        raise ValueError("scene and brief episode_id mismatch")

    brief_character = {
        item.character_id: item
        for item in brief.characters
    }
    character_registry = {
        row["character_id"]: row
        for row in registry_snapshot.get("characters", [])
    }
    profiles = {
        row["voice_profile_id"]: row
        for row in registry_snapshot.get("voice_profiles", [])
    }

    requests: list[VoiceRequest] = []
    for render_scene in scene.scenes:
        for line in sorted(render_scene.dialogue, key=lambda item: item.start_ms):
            character = brief_character.get(line.speaker)
            if character is None:
                raise ValueError(
                    f"dialogue speaker not present in content brief: {line.speaker}"
                )
            registered = character_registry.get(line.speaker) or {}
            profile_id = (
                registered.get("voice_profile_id")
                or character.voice_profile_id
            )
            profile = profiles.get(profile_id)

            if profile is None:
                status = "PROFILE_REQUIRED"
                adapter_hint = "UNBOUND"
                source_type = "UNKNOWN"
                provenance = None
                usage_rights = "REVIEW_REQUIRED"
            else:
                adapter_hint = profile.get("adapter_hint") or "UNBOUND"
                source_type = profile.get("source_type") or "UNKNOWN"
                provenance = profile.get("provenance")
                usage_rights = profile.get("usage_rights") or "REVIEW_REQUIRED"
                if usage_rights == "BLOCKED":
                    status = "BLOCKED"
                elif usage_rights != "APPROVED":
                    status = "RIGHTS_REVIEW"
                elif adapter_hint == "UNBOUND":
                    status = "ADAPTER_REQUIRED"
                else:
                    status = "READY_FOR_SYNTHESIS"

            requests.append(
                VoiceRequest(
                    request_key=_request_key(
                        brief.episode_id,
                        render_scene.scene_id,
                        line.line_id,
                        line.text,
                        profile_id,
                    ),
                    voice_asset_id=line.voice_asset_id,
                    scene_id=render_scene.scene_id,
                    line_id=line.line_id,
                    speaker=line.speaker,
                    text=line.text,
                    text_sha256=hashlib.sha256(
                        line.text.encode("utf-8")
                    ).hexdigest(),
                    voice_profile_id=profile_id,
                    language=brief.language,
                    start_ms=line.start_ms,
                    end_ms=line.end_ms,
                    adapter_hint=adapter_hint,
                    source_type=source_type,
                    provenance=provenance,
                    usage_rights=usage_rights,
                    status=status,
                )
            )

    status_counts = {
        status: sum(item.status == status for item in requests)
        for status in {
            "READY_FOR_SYNTHESIS",
            "PROFILE_REQUIRED",
            "RIGHTS_REVIEW",
            "ADAPTER_REQUIRED",
            "BLOCKED",
        }
    }
    return VoicePlan(
        episode_id=brief.episode_id,
        scene_sha256=manifest_sha256(scene),
        registry_snapshot_sha256=registry_snapshot["snapshot_sha256"],
        requests=requests,
        ready_count=status_counts["READY_FOR_SYNTHESIS"],
        profile_required_count=status_counts["PROFILE_REQUIRED"],
        review_required_count=status_counts["RIGHTS_REVIEW"],
        adapter_required_count=status_counts["ADAPTER_REQUIRED"],
        blocked_count=status_counts["BLOCKED"],
        ready_for_synthesis=(
            len(requests) > 0
            and status_counts["READY_FOR_SYNTHESIS"] == len(requests)
        ),
    )
