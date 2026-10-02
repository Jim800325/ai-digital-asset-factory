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
