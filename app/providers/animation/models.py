from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def canonical_json(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def manifest_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ShrimpModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CharacterBrief(ShrimpModel):
    character_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,31}$")
    display_name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=160)
    voice_profile_id: str = Field(
        default="default",
        pattern=r"^[a-z0-9][a-z0-9_-]{1,63}$",
    )


class ContentBrief(ShrimpModel):
    schema_version: Literal["v0.1"] = "v0.1"
    episode_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    title: str = Field(min_length=3, max_length=160)
    premise: str = Field(min_length=20, max_length=4000)
    audience: str = Field(min_length=2, max_length=200)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    target_duration_ms: int = Field(default=60000, ge=10000, le=600000)
    scene_count: int = Field(default=3, ge=2, le=12)
    characters: list[CharacterBrief] = Field(min_length=2, max_length=8)
    background_hints: list[str] = Field(
        default_factory=lambda: ["interior"],
        min_length=1,
        max_length=12,
    )
    style_tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("background_hints", "style_tags")
    @classmethod
    def _clean_string_list(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip()[:100] for value in values if str(value).strip()]
        if not cleaned and values:
            raise ValueError("list entries must not be blank")
        return cleaned

    @model_validator(mode="after")
    def _unique_characters(self):
        ids = [character.character_id for character in self.characters]
        if len(ids) != len(set(ids)):
            raise ValueError("character_id values must be unique")
        return self


class StoryBeat(ShrimpModel):
    beat_id: str = Field(pattern=r"^b\d{3}$")
    scene_index: int = Field(ge=1, le=99)
    phase: Literal["SETUP", "ESCALATION", "TURN", "RESOLUTION"]
    objective: str = Field(min_length=3, max_length=800)
    conflict: str = Field(min_length=3, max_length=800)
    turn: str = Field(min_length=3, max_length=800)
    featured_character_ids: list[str] = Field(min_length=1, max_length=8)


class StoryManifest(ShrimpModel):
    schema_version: Literal["story-v0.1"] = "story-v0.1"
    planner_version: Literal["story-planner-v0.1-deterministic"] = (
        "story-planner-v0.1-deterministic"
    )
    episode_id: str
    title: str
    language: str
    brief_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    logline: str
    beats: list[StoryBeat] = Field(min_length=2, max_length=12)


class DialogueLine(ShrimpModel):
    line_id: str = Field(pattern=r"^l\d{3}-\d{2}$")
    speaker: str
    text: str = Field(min_length=1, max_length=500)
    emotion: Literal["neutral", "curious", "concerned", "confident", "surprised"]
    order: int = Field(ge=1, le=20)


class ScriptScene(ShrimpModel):
    scene_id: str = Field(pattern=r"^s\d{3}$")
    beat_id: str = Field(pattern=r"^b\d{3}$")
    summary: str = Field(min_length=3, max_length=1000)
    dialogue: list[DialogueLine] = Field(min_length=2, max_length=20)


class ScriptManifest(ShrimpModel):
    schema_version: Literal["script-v0.1"] = "script-v0.1"
    planner_version: Literal["script-planner-v0.1-deterministic"] = (
        "script-planner-v0.1-deterministic"
    )
    episode_id: str
    language: str
    story_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenes: list[ScriptScene] = Field(min_length=2, max_length=12)


class Resolution(ShrimpModel):
    width: int = Field(default=1920, ge=320, le=7680)
    height: int = Field(default=1080, ge=240, le=4320)


class CameraCue(ShrimpModel):
    type: Literal[
        "static", "pan", "zoom", "push_in", "pull_out",
        "shake", "focus_left", "focus_right",
    ]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=1)
    from_scale: float = Field(default=1.0, ge=0.25, le=4.0)
    to_scale: float = Field(default=1.0, ge=0.25, le=4.0)


class CharacterAction(ShrimpModel):
    character_id: str
    asset_variant: str = "idle"
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    action: Literal[
        "idle", "talk", "walk_left", "walk_right", "enter", "exit",
        "jump", "shake", "nod", "bow", "turn", "scale_pulse",
        "hit_reaction", "surprised", "angry", "laugh",
    ]
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=1)


class SceneDialogue(ShrimpModel):
    line_id: str
    speaker: str
    text: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=1)
    voice_asset_id: str


class RenderScene(ShrimpModel):
    scene_id: str
    duration_ms: int = Field(ge=1000, le=120000)
    background_id: str
    transition: Literal["cut", "fade", "crossfade", "slide", "flash"]
    camera: list[CameraCue] = Field(min_length=1, max_length=8)
    characters: list[CharacterAction] = Field(min_length=1, max_length=32)
    dialogue: list[SceneDialogue] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def _timeline_bounds(self):
        for cue in self.camera:
            if cue.end_ms > self.duration_ms or cue.end_ms <= cue.start_ms:
                raise ValueError("camera cue is outside scene duration")
        for action in self.characters:
            if action.end_ms > self.duration_ms or action.end_ms <= action.start_ms:
                raise ValueError("character action is outside scene duration")
        for line in self.dialogue:
            if line.end_ms > self.duration_ms or line.end_ms <= line.start_ms:
                raise ValueError("dialogue line is outside scene duration")
        return self


class SceneManifest(ShrimpModel):
    schema_version: Literal["scene-v0.1"] = "scene-v0.1"
    planner_version: Literal["scene-planner-v0.1-deterministic"] = (
        "scene-planner-v0.1-deterministic"
    )
    episode_id: str
    fps: int = Field(default=30, ge=12, le=120)
    resolution: Resolution = Field(default_factory=Resolution)
    script_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scenes: list[RenderScene] = Field(min_length=2, max_length=12)


class ReusableAssetRef(ShrimpModel):
    asset_key: str
    asset_kind: str
    storage_uri: str
    media_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    license_id: str
    provenance: str
    usage_rights: Literal["APPROVED", "REVIEW_REQUIRED", "BLOCKED"]


class AssetRequirement(ShrimpModel):
    requirement_key: str
    asset_kind: Literal["CHARACTER", "BACKGROUND"]
    logical_id: str
    variant: str | None = None
    status: Literal[
        "REUSE_READY",
        "GENERATION_REQUIRED",
        "RIGHTS_REVIEW",
        "BLOCKED",
    ]
    reusable_asset: ReusableAssetRef | None = None


class CapabilityRequirement(ShrimpModel):
    capability_kind: Literal["ACTION", "CAMERA"]
    capability_key: str
    renderer_primitive: str | None = None
    status: Literal["SUPPORTED", "BLOCKED"]


class AssetPlan(ShrimpModel):
    schema_version: Literal["asset-plan-v0.1"] = "asset-plan-v0.1"
    planner_version: Literal["asset-planner-v0.1-deterministic"] = (
        "asset-planner-v0.1-deterministic"
    )
    episode_id: str
    scene_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requirements: list[AssetRequirement]
    capabilities: list[CapabilityRequirement]
    reuse_ready_count: int = Field(ge=0)
    generation_required_count: int = Field(ge=0)
    review_required_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    ready_for_asset_execution: bool


class VoiceRequest(ShrimpModel):
    request_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    voice_asset_id: str
    scene_id: str
    line_id: str
    speaker: str
    text: str
    text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    voice_profile_id: str
    language: str
    start_ms: int = Field(ge=0)
    end_ms: int = Field(ge=1)
    adapter_hint: str
    source_type: Literal[
        "SYNTHETIC",
        "SELF_RECORDED",
        "LICENSED",
        "CLONED_WITH_CONSENT",
        "UNKNOWN",
    ]
    provenance: str | None = None
    usage_rights: Literal["APPROVED", "REVIEW_REQUIRED", "BLOCKED"]
    status: Literal[
        "READY_FOR_SYNTHESIS",
        "PROFILE_REQUIRED",
        "RIGHTS_REVIEW",
        "ADAPTER_REQUIRED",
        "BLOCKED",
    ]

    @model_validator(mode="after")
    def _valid_voice_window(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("voice request end_ms must be greater than start_ms")
        return self


class VoicePlan(ShrimpModel):
    schema_version: Literal["voice-plan-v0.1"] = "voice-plan-v0.1"
    planner_version: Literal["voice-planner-v0.1-deterministic"] = (
        "voice-planner-v0.1-deterministic"
    )
    episode_id: str
    scene_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    requests: list[VoiceRequest]
    ready_count: int = Field(ge=0)
    profile_required_count: int = Field(ge=0)
    review_required_count: int = Field(ge=0)
    adapter_required_count: int = Field(ge=0)
    blocked_count: int = Field(ge=0)
    ready_for_synthesis: bool


class ArtifactProvenance(ShrimpModel):
    adapter_key: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=100)
    provider_request_id: str | None = Field(default=None, max_length=300)
    source_reference: str = Field(min_length=1, max_length=2000)
    source_type: Literal[
        "REUSABLE_REGISTRY",
        "COMFYUI_GENERATED",
        "TTS_GENERATED",
        "FIXTURE",
    ]
    provenance: str = Field(min_length=1, max_length=4000)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class VerifiedArtifact(ShrimpModel):
    logical_key: str
    artifact_kind: Literal["CHARACTER", "BACKGROUND", "VOICE"]
    source_mode: Literal["REUSED", "GENERATED"]
    plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    storage_uri: str
    media_type: str
    byte_size: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    license_id: str
    usage_rights: Literal["APPROVED"]
    provenance: ArtifactProvenance
    duration_ms: int | None = Field(default=None, gt=0)


class AssetArtifactManifest(ShrimpModel):
    schema_version: Literal["asset-artifacts-v0.1"] = "asset-artifacts-v0.1"
    provider_version: Literal["shrimp-animation-v0.1"] = "shrimp-animation-v0.1"
    episode_id: str
    asset_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: list[VerifiedArtifact] = Field(min_length=1)
    reused_count: int = Field(ge=0)
    generated_count: int = Field(ge=0)

    @model_validator(mode="after")
    def _asset_manifest_consistency(self):
        if any(item.artifact_kind == "VOICE" for item in self.artifacts):
            raise ValueError("Asset manifest cannot contain voice artifacts")
        if self.reused_count != sum(
            item.source_mode == "REUSED" for item in self.artifacts
        ):
            raise ValueError("Asset manifest reused_count mismatch")
        if self.generated_count != sum(
            item.source_mode == "GENERATED" for item in self.artifacts
        ):
            raise ValueError("Asset manifest generated_count mismatch")
        return self


class VoiceArtifactManifest(ShrimpModel):
    schema_version: Literal["voice-artifacts-v0.1"] = "voice-artifacts-v0.1"
    provider_version: Literal["shrimp-animation-v0.1"] = "shrimp-animation-v0.1"
    episode_id: str
    voice_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifacts: list[VerifiedArtifact] = Field(min_length=1)
    total_duration_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def _voice_manifest_consistency(self):
        if any(item.artifact_kind != "VOICE" for item in self.artifacts):
            raise ValueError("Voice manifest may contain only voice artifacts")
        total = sum(int(item.duration_ms or 0) for item in self.artifacts)
        if self.total_duration_ms != total:
            raise ValueError("Voice manifest total_duration_ms mismatch")
        return self


class TimelineMediaRef(ShrimpModel):
    logical_key: str
    storage_uri: str
    media_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TimelineCameraCue(ShrimpModel):
    type: Literal[
        "static", "pan", "zoom", "push_in", "pull_out",
        "shake", "focus_left", "focus_right",
    ]
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    from_scale: float = Field(ge=0.25, le=4.0)
    to_scale: float = Field(ge=0.25, le=4.0)

    @model_validator(mode="after")
    def _frame_order(self):
        if self.end_frame <= self.start_frame:
            raise ValueError("camera end_frame must be greater than start_frame")
        return self


class TimelineCharacterCue(ShrimpModel):
    character_id: str
    asset: TimelineMediaRef
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    action: str
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)

    @model_validator(mode="after")
    def _frame_order(self):
        if self.end_frame <= self.start_frame:
            raise ValueError("character end_frame must be greater than start_frame")
        return self


class TimelineAudioCue(ShrimpModel):
    voice_asset_id: str
    line_id: str
    speaker: str
    media: TimelineMediaRef
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    measured_duration_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def _frame_order(self):
        if self.end_frame <= self.start_frame:
            raise ValueError("audio end_frame must be greater than start_frame")
        return self


class SubtitleCue(ShrimpModel):
    line_id: str
    speaker: str
    text: str
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    safe_area_bottom: float = Field(default=0.08, ge=0.0, le=0.4)
    max_width: float = Field(default=0.84, gt=0.1, le=1.0)

    @model_validator(mode="after")
    def _frame_order(self):
        if self.end_frame <= self.start_frame:
            raise ValueError("subtitle end_frame must be greater than start_frame")
        return self


class AnimationSceneTimeline(ShrimpModel):
    scene_id: str
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)
    duration_frames: int = Field(gt=0)
    background: TimelineMediaRef
    transition: Literal["cut", "fade", "crossfade", "slide", "flash"]
    camera: list[TimelineCameraCue] = Field(min_length=1)
    characters: list[TimelineCharacterCue] = Field(min_length=1)
    audio: list[TimelineAudioCue] = Field(min_length=1)
    subtitles: list[SubtitleCue] = Field(min_length=1)

    @model_validator(mode="after")
    def _scene_frame_consistency(self):
        if self.end_frame - self.start_frame != self.duration_frames:
            raise ValueError("scene duration_frames mismatch")
        for group in (self.camera, self.characters, self.audio, self.subtitles):
            for cue in group:
                if cue.start_frame < 0 or cue.end_frame > self.duration_frames:
                    raise ValueError("scene cue falls outside scene duration")
        return self


class AnimationTimelineManifest(ShrimpModel):
    schema_version: Literal["animation-timeline-v0.1"] = "animation-timeline-v0.1"
    planner_version: Literal["animation-planner-v0.1-deterministic"] = (
        "animation-planner-v0.1-deterministic"
    )
    episode_id: str
    fps: int = Field(ge=12, le=120)
    resolution: Resolution
    scene_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    voice_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_duration_frames: int = Field(gt=0)
    scenes: list[AnimationSceneTimeline] = Field(min_length=1)

    @model_validator(mode="after")
    def _timeline_consistency(self):
        cursor = 0
        for scene in self.scenes:
            if scene.start_frame != cursor:
                raise ValueError("animation scenes must be contiguous")
            cursor = scene.end_frame
        if cursor != self.total_duration_frames:
            raise ValueError("total_duration_frames mismatch")
        return self


class RemotionCompositionPayload(ShrimpModel):
    composition_id: str = Field(min_length=1, max_length=100)
    props_uri: str = Field(min_length=1, max_length=2000)
    props_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    project_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adapter_key: str = Field(min_length=1, max_length=100)
    adapter_version: str = Field(min_length=1, max_length=100)
    props: dict
