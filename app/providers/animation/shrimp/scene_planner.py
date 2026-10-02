from app.providers.animation.models import (
    CameraCue,
    CharacterAction,
    ContentBrief,
    RenderScene,
    SceneDialogue,
    SceneManifest,
    ScriptManifest,
    manifest_sha256,
)


_CAMERA_TYPES = ("static", "zoom", "push_in", "focus_left", "focus_right")
_TRANSITIONS = ("cut", "fade", "crossfade")


def _scene_durations(total_ms: int, count: int) -> list[int]:
    base = total_ms // count
    remainder = total_ms - base * count
    return [
        base + (1 if index < remainder else 0)
        for index in range(count)
    ]


def plan_scenes(
    brief: ContentBrief,
    script: ScriptManifest,
) -> SceneManifest:
    if not isinstance(brief, ContentBrief):
        brief = ContentBrief.model_validate(brief)
    if not isinstance(script, ScriptManifest):
        script = ScriptManifest.model_validate(script)
    if script.episode_id != brief.episode_id:
        raise ValueError("script and brief episode_id mismatch")
    if len(script.scenes) != brief.scene_count:
        raise ValueError("script scene count does not match content brief")

    durations = _scene_durations(
        brief.target_duration_ms,
        len(script.scenes),
    )
    scenes = []
    for index, (script_scene, duration_ms) in enumerate(
        zip(script.scenes, durations),
        start=1,
    ):
        lines = sorted(script_scene.dialogue, key=lambda line: line.order)
        usable_start = min(900, max(100, duration_ms // 10))
        usable_end = max(usable_start + 1000, duration_ms - 700)
        slot = max(700, (usable_end - usable_start) // len(lines))
        dialogue = []
        actions = []
        for line_index, line in enumerate(lines):
            start = usable_start + slot * line_index
            end = (
                usable_end
                if line_index == len(lines) - 1
                else min(usable_end, start + slot - 100)
            )
            end = max(start + 300, end)
            dialogue.append(
                SceneDialogue(
                    line_id=line.line_id,
                    speaker=line.speaker,
                    text=line.text,
                    start_ms=start,
                    end_ms=end,
                    voice_asset_id=f"voice-{script_scene.scene_id}-{line_index + 1:02d}",
                )
            )
            actions.append(
                CharacterAction(
                    character_id=line.speaker,
                    asset_variant="idle",
                    x=0.32 if line_index % 2 == 0 else 0.68,
                    y=0.74,
                    action="talk",
                    start_ms=start,
                    end_ms=end,
                )
            )

        camera_type = _CAMERA_TYPES[(index - 1) % len(_CAMERA_TYPES)]
        scenes.append(
            RenderScene(
                scene_id=script_scene.scene_id,
                duration_ms=duration_ms,
                background_id=brief.background_hints[
                    (index - 1) % len(brief.background_hints)
                ],
                transition=_TRANSITIONS[(index - 1) % len(_TRANSITIONS)],
                camera=[
                    CameraCue(
                        type=camera_type,
                        start_ms=0,
                        end_ms=duration_ms,
                        from_scale=1.0,
                        to_scale=1.06 if camera_type != "static" else 1.0,
                    )
                ],
                characters=actions,
                dialogue=dialogue,
            )
        )

    return SceneManifest(
        episode_id=brief.episode_id,
        script_sha256=manifest_sha256(script),
        scenes=scenes,
    )
